"""Step 6b: IceGuard's own SQLite store (Python's built-in sqlite3, no server).

Two tables:
  analysis_cache  SqlAnalysis results, keyed by a hash of the SQL, the catalog
                  facts it depends on and the analyser's source code. Any change
                  to one of those gives a new key, so a cached result is never stale.
  checks          one row per reuse check (from MCP or the hook): the history the
                  Milestone 3 dashboard reads ("duplicates prevented", overrides).

WAL mode and a busy timeout let the MCP server and the hook write at the same time.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from iceguard.models import Decision, ReusePlan, SqlAnalysis

SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_cache (
    key        TEXT PRIMARY KEY,
    analysis   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at        TEXT NOT NULL,
    source            TEXT NOT NULL,   -- 'mcp' | 'hook'
    catalog           TEXT NOT NULL,   -- 'file' | 'clickhouse'
    file_path         TEXT,
    proposed_table    TEXT,
    sql_sha256        TEXT NOT NULL,
    decision          TEXT NOT NULL,
    recommended_table TEXT,
    action            TEXT NOT NULL,   -- 'reported' | 'blocked' | 'allowed' | 'overridden'
    plan              TEXT NOT NULL    -- the full ReusePlan as JSON
);
CREATE INDEX IF NOT EXISTS checks_checked_at ON checks (checked_at);
"""


class CheckRecord(BaseModel):
    id: int
    checked_at: str
    source: str
    catalog: str
    file_path: str | None
    proposed_table: str | None
    decision: Decision
    recommended_table: str | None
    action: str


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=5.0)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # --- AnalysisCache --------------------------------------------------------

    def get(self, key: str) -> SqlAnalysis | None:
        row = self._db.execute(
            "SELECT analysis FROM analysis_cache WHERE key = ?", (key,)
        ).fetchone()
        return SqlAnalysis.model_validate_json(row[0]) if row else None

    def put(self, key: str, analysis: SqlAnalysis) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO analysis_cache (key, analysis, created_at) "
                "VALUES (?, ?, ?)",
                (key, analysis.model_dump_json(), _now()),
            )

    # --- Check history --------------------------------------------------------

    def record_check(
        self,
        *,
        sql: str,
        plan: ReusePlan,
        source: str,
        catalog: str,
        action: str,
        file_path: str | None = None,
    ) -> int:
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO checks (checked_at, source, catalog, file_path, proposed_table, "
                "sql_sha256, decision, recommended_table, action, plan) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _now(),
                    source,
                    catalog,
                    file_path,
                    plan.proposed_table,
                    hashlib.sha256(sql.encode()).hexdigest(),
                    plan.decision.value,
                    plan.recommended_table,
                    action,
                    plan.model_dump_json(),
                ),
            )
        return int(cursor.lastrowid)

    def recent_checks(self, limit: int = 20) -> list[CheckRecord]:
        rows = self._db.execute(
            "SELECT id, checked_at, source, catalog, file_path, proposed_table, decision, "
            "recommended_table, action FROM checks ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        fields = list(CheckRecord.model_fields)
        return [CheckRecord(**dict(zip(fields, row, strict=True))) for row in rows]

    def get_plan(self, check_id: int) -> ReusePlan | None:
        row = self._db.execute("SELECT plan FROM checks WHERE id = ?", (check_id,)).fetchone()
        return ReusePlan.model_validate_json(row[0]) if row else None

    def action_counts(self) -> dict[str, int]:
        return dict(self._db.execute("SELECT action, count(*) FROM checks GROUP BY action"))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
