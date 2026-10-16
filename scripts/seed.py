"""Load the demo warehouse into the local ClickHouse (scripts/clickhouse.sh start first).

  1. Recreate the `raw` and `analytics` databases.
  2. Fill the source tables (seed/01_raw_tables.sql).
  3. Run pipelines/existing/*.sql in dependency order: the repo's SQL is
     what creates the analytics tables.
  4. Add table descriptions (seed/02_comments.sql).
  5. Simulate usage for Milestone 2: tagged pipeline rebuilds and reads by a
     dashboard, an analyst, an ML job and an untagged client. The zombie
     table is rebuilt but never read. Then flush the query log.

Usage: uv run python scripts/seed.py [--host 127.0.0.1] [--port 8123]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import clickhouse_connect
import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parent.parent
PIPELINES = ROOT / "pipelines" / "existing"
DATABASES = ("raw", "analytics")

READS = [
    # (actor tag or None, SQL, repetitions)
    (
        {"actor": "dashboard", "dashboard": "merchant_delays"},
        "SELECT merchant_id, avg_delay_minutes FROM analytics.agg_merchant_daily_delays "
        "WHERE delivery_date >= '2026-09-01' ORDER BY avg_delay_minutes DESC LIMIT 10",
        3,
    ),
    (
        {"actor": "analyst", "user": "ops_analytics"},
        "SELECT count() FROM analytics.fact_deliveries WHERE delay_minutes > 30",
        2,
    ),
    (
        {"actor": "ml", "job": "eta_model_features"},
        "SELECT dasher_id, avg(delay_minutes) FROM analytics.fact_deliveries GROUP BY dasher_id",
        1,
    ),
    (None, "SELECT count() FROM analytics.fact_deliveries", 1),
]
ZOMBIE_REBUILDS = 2  # extra nightly runs of the never-read table


def statements(path: Path) -> list[str]:
    return [s.sql("clickhouse") for s in sqlglot.parse(path.read_text(), read="clickhouse") if s]


def pipeline_order() -> list[Path]:
    """Order pipeline files so each runs after the tables it reads exist."""
    targets: dict[str, Path] = {}
    reads: dict[Path, set[str]] = {}
    for path in sorted(PIPELINES.glob("*.sql")):
        ast = sqlglot.parse_one(path.read_text(), read="clickhouse")
        target = ast.find(exp.Table)
        targets[f"{target.db}.{target.name}"] = path
        reads[path] = {f"{t.db}.{t.name}" for t in ast.expression.find_all(exp.Table)}

    ordered: list[Path] = []
    pending = dict(reads)
    while pending:
        ready = [
            p
            for p, deps in pending.items()
            if all(targets.get(d) in ordered for d in deps if d in targets)
        ]
        if not ready:
            raise SystemExit(f"Circular pipeline dependencies: {sorted(map(str, pending))}")
        for p in sorted(ready):
            ordered.append(p)
            del pending[p]
    return ordered


def airflow_tag(path: Path) -> dict[str, str]:
    return {"log_comment": json.dumps({"actor": "airflow", "dag": path.stem})}


def rebuild(client, path: Path) -> None:
    """Re-run a pipeline the way a nightly job would: truncate, then insert."""
    ast = sqlglot.parse_one(path.read_text(), read="clickhouse")
    target = ast.find(exp.Table)
    name = f"{target.db}.{target.name}"
    client.command(f"TRUNCATE TABLE {name}", settings=airflow_tag(path))
    client.command(
        f"INSERT INTO {name} {ast.expression.sql('clickhouse')}", settings=airflow_tag(path)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()
    client = clickhouse_connect.get_client(host=args.host, port=args.port)
    started = time.monotonic()

    for db in DATABASES:
        client.command(f"DROP DATABASE IF EXISTS {db} SYNC")
        client.command(f"CREATE DATABASE {db}")

    for sql in statements(ROOT / "seed" / "01_raw_tables.sql"):
        client.command(sql, settings={"log_comment": json.dumps({"actor": "seed"})})

    for path in pipeline_order():
        client.command(path.read_text(), settings=airflow_tag(path))
        print(f"  built {path.name}")

    for sql in statements(ROOT / "seed" / "02_comments.sql"):
        client.command(sql)

    for _ in range(ZOMBIE_REBUILDS):
        rebuild(client, PIPELINES / "zombie_promo_clicks_2024.sql")
    for tag, sql, times in READS:
        settings = {"log_comment": json.dumps(tag)} if tag else {}
        for _ in range(times):
            client.query(sql, settings=settings)

    client.command("SYSTEM FLUSH LOGS")

    rows = client.query(
        "SELECT database, name, total_rows, formatReadableSize(total_bytes) FROM system.tables "
        "WHERE database IN ('raw', 'analytics') ORDER BY database, name"
    ).result_rows
    for db, name, n, size in rows:
        print(f"  {db}.{name:28} {n:>9,} rows  {size}")
    print(f"Seeded in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
