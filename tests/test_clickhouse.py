"""Integration tests against the local ClickHouse (scripts/clickhouse.sh start + scripts/seed.py).

Skipped automatically when the server is not running.
"""

import json

import pytest

from iceguard.catalog import SchemaFileCatalog
from iceguard.models import Decision
from iceguard.service import plan_table_reuse

from .conftest import PROPOSED_DIR, REPO_ROOT

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    clickhouse_connect = pytest.importorskip("clickhouse_connect")
    from clickhouse_connect.driver.exceptions import OperationalError

    try:
        c = clickhouse_connect.get_client(host="127.0.0.1", port=8123)
    except OperationalError:
        pytest.skip("ClickHouse is not running (scripts/clickhouse.sh start)")
    if not c.command("EXISTS TABLE analytics.fact_deliveries"):
        pytest.skip("Demo data not loaded (uv run python scripts/seed.py)")
    yield c
    c.close()


@pytest.fixture(scope="module")
def ch_catalog(client):
    from iceguard.clickhouse_catalog import ClickHouseCatalog

    return ClickHouseCatalog(REPO_ROOT)


# --- Discovery matches the hand-written catalog --------------------------------


def test_discovers_the_same_tables(ch_catalog, catalog: SchemaFileCatalog) -> None:
    assert {t.name for t in ch_catalog.list_tables()} == {t.name for t in catalog.list_tables()}


def test_discovered_columns_and_types_match_schema_file(
    ch_catalog, catalog: SchemaFileCatalog
) -> None:
    for expected in catalog.list_tables():
        found = ch_catalog.get_table(expected.name)
        assert found.columns == expected.columns, expected.name


def test_discovered_layout_contracts_and_producers(ch_catalog, catalog: SchemaFileCatalog) -> None:
    for expected in catalog.list_tables():
        found = ch_catalog.get_table(expected.name)
        assert found.order_by == expected.order_by, expected.name
        assert found.partition_by == expected.partition_by, expected.name
        assert found.unique_key == expected.unique_key, expected.name
        assert found.current_state == expected.current_state, expected.name
        assert found.produced_by == expected.produced_by, expected.name
        assert found.description == expected.description, expected.name
        assert found.total_rows and found.total_rows > 0, expected.name


# --- The planted cases give the same answers on real metadata ------------------


@pytest.mark.parametrize(
    ("filename", "decision", "table"),
    [
        ("01_exact_duplicate.sql", Decision.REUSE, "analytics.fact_deliveries"),
        ("02_missing_filter.sql", Decision.NEW_TABLE, None),
        ("03_different_grain.sql", Decision.NEW_TABLE, None),
        ("04_evolve_schema.sql", Decision.EVOLVE_SCHEMA, "analytics.fact_deliveries"),
    ],
)
def test_planted_case_on_clickhouse(ch_catalog, filename, decision, table) -> None:
    plan = plan_table_reuse((PROPOSED_DIR / filename).read_text(), ch_catalog)
    assert plan.decision == decision
    assert plan.recommended_table == table


# --- The data is what the planted cases assume ---------------------------------


def test_fact_deliveries_holds_only_delivered_rows(client) -> None:
    statuses = client.query("SELECT DISTINCT status FROM analytics.fact_deliveries").result_rows
    assert statuses == [("DELIVERED",)]
    assert client.command("SELECT countIf(status != 'DELIVERED') > 0 FROM raw.deliveries") == 1


def test_weather_has_one_row_per_zone_and_hour(client) -> None:
    assert client.command("SELECT count() = uniqExact(zone_id, hour) FROM raw.weather_hourly") == 1


def test_fact_deliveries_spans_several_partitions(client) -> None:
    partitions = client.command(
        "SELECT uniqExact(partition) FROM system.parts "
        "WHERE database = 'analytics' AND table = 'fact_deliveries' AND active"
    )
    assert partitions >= 2


def test_query_log_has_tagged_usage(client) -> None:
    rows = client.query(
        "SELECT DISTINCT log_comment FROM system.query_log "
        "WHERE type = 'QueryFinish' AND log_comment != ''"
    ).result_rows
    actors = {json.loads(c).get("actor") for (c,) in rows}
    assert {"airflow", "dashboard", "analyst", "ml"} <= actors


# --- IceGuard's decisions are true on the real data -------------------------------
#
# Each proposal is executed and compared with the table IceGuard points to, using
# the row count and an order-independent checksum, sum(cityHash64(columns)).


def _select(sql: str) -> str:
    """The SELECT inside CREATE TABLE ... AS SELECT (or the SQL itself)."""
    import sqlglot

    ast = sqlglot.parse_one(sql, read="clickhouse")
    return (ast.expression or ast).sql("clickhouse")


def _fingerprint(client, source: str, columns: list[str]) -> tuple[int, int]:
    exprs = ", ".join(columns)
    return tuple(
        client.query(f"SELECT count(), sum(cityHash64({exprs})) FROM {source}").result_rows[0]
    )


def _proposal(name: str) -> str:
    return (PROPOSED_DIR / name).read_text()


REUSE_CASES = {
    "planted 01, different alias and column order": (
        _proposal("01_exact_duplicate.sql"),
        ["delivery_id", "delivery_date", "delay_minutes"],
        "analytics.fact_deliveries",
        ["delivery_id", "delivery_date", "delay_minutes"],
    ),
    "CTE with renamed columns": (
        "WITH x AS (SELECT * FROM raw.deliveries WHERE status = 'DELIVERED') "
        "SELECT delivery_id AS id, "
        "dateDiff('minute', promised_time, actual_time) AS late_by FROM x",
        ["id", "late_by"],
        "analytics.fact_deliveries",
        ["delivery_id", "delay_minutes"],
    ),
    "filter written as IN": (
        "SELECT delivery_id, toDate(actual_time) AS delivery_date FROM raw.deliveries "
        "WHERE status IN ('DELIVERED')",
        ["delivery_id", "delivery_date"],
        "analytics.fact_deliveries",
        ["delivery_id", "delivery_date"],
    ),
    "aggregate rebuilt from raw": (
        "SELECT merchant_id, toDate(actual_time) AS delivery_date, "
        "avg(dateDiff('minute', promised_time, actual_time)) AS avg_delay_minutes "
        "FROM raw.deliveries WHERE status = 'DELIVERED' GROUP BY merchant_id, delivery_date",
        ["merchant_id", "delivery_date", "round(avg_delay_minutes, 6)"],
        "analytics.agg_merchant_daily_delays",
        ["merchant_id", "delivery_date", "round(avg_delay_minutes, 6)"],
    ),
}


@pytest.mark.parametrize("case", REUSE_CASES, ids=list(REUSE_CASES))
def test_reuse_means_identical_data(client, ch_catalog, case) -> None:
    sql, proposal_columns, table, table_columns = REUSE_CASES[case]
    plan = plan_table_reuse(sql, ch_catalog)
    assert (plan.decision, plan.recommended_table) == (Decision.REUSE, table)

    proposal = _fingerprint(client, f"({_select(sql)})", proposal_columns)
    assert proposal == _fingerprint(client, table, table_columns)
    assert proposal[0] > 0


def test_missing_filter_really_needs_rows_fact_lacks(client) -> None:
    proposal = _select(_proposal("02_missing_filter.sql"))
    rows = client.query(
        f"SELECT count(), countIf(status != 'DELIVERED') FROM ({proposal}) AS p "
        "JOIN raw.deliveries AS d USING delivery_id"
    ).result_rows[0]
    fact_rows = client.command("SELECT count() FROM analytics.fact_deliveries")
    assert rows[0] > fact_rows
    assert rows[0] - fact_rows == rows[1]  # exactly the non-delivered rows are extra


def test_evolve_schema_join_keeps_one_row_per_delivery(client) -> None:
    proposal = f"({_select(_proposal('04_evolve_schema.sql'))})"
    rows, unique = client.query(
        f"SELECT count(), uniqExact(delivery_id) FROM {proposal}"
    ).result_rows[0]
    assert rows == unique
    shared = ["delivery_id", "delivery_date", "delay_minutes"]
    assert _fingerprint(client, proposal, shared) == _fingerprint(
        client, "analytics.fact_deliveries", shared
    )


def test_different_grain_is_one_row_per_dasher_per_day(client) -> None:
    proposal = f"({_select(_proposal('03_different_grain.sql'))})"
    rows, unique = client.query(
        f"SELECT count(), uniqExact(dasher_id, delivery_date) FROM {proposal}"
    ).result_rows[0]
    assert rows == unique
    assert rows not in {
        client.command("SELECT count() FROM analytics.fact_deliveries"),
        client.command("SELECT count() FROM analytics.agg_merchant_daily_delays"),
    }


def test_extra_filter_suggestion_returns_the_same_rows(client, ch_catalog) -> None:
    sql = (
        "SELECT delivery_id, dateDiff('minute', promised_time, actual_time) AS delay_minutes "
        "FROM raw.deliveries WHERE status = 'DELIVERED' AND toDate(actual_time) >= '2026-09-01'"
    )
    plan = plan_table_reuse(sql, ch_catalog)
    logic = next(c for c in plan.assessments[0].checks if c.name == "logic")
    assert "read analytics.fact_deliveries WHERE delivery_date >= '2026-09-01'" in logic.reasons[0]

    suggested = "(SELECT * FROM analytics.fact_deliveries WHERE delivery_date >= '2026-09-01')"
    columns = ["delivery_id", "delay_minutes"]
    assert _fingerprint(client, f"({sql})", columns) == _fingerprint(client, suggested, columns)


def _contracts() -> dict:
    import tomllib

    with (REPO_ROOT / "catalog" / "contracts.toml").open("rb") as f:
        return tomllib.load(f)["tables"]


@pytest.mark.parametrize("table", sorted(_contracts()))
def test_declared_unique_key_holds_in_the_data(client, table: str) -> None:
    key = ", ".join(_contracts()[table]["unique_key"])
    rows, unique = client.query(f"SELECT count(), uniqExact({key}) FROM {table}").result_rows[0]
    assert rows == unique > 0
