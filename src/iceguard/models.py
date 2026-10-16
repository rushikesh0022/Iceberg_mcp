"""Typed inputs and results shared by every IceGuard module."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

# --- Catalog -----------------------------------------------------------------


class Column(BaseModel):
    name: str
    type: str


class Table(BaseModel):
    """One table in the catalog, with the SQL file that produces it (if known)."""

    name: str  # qualified name, e.g. "analytics.fact_deliveries"
    columns: list[Column]
    description: str = ""
    order_by: list[str] = Field(default_factory=list)
    partition_by: str | None = None
    # Columns declared to identify one row. ClickHouse does not enforce this,
    # so it comes from the schema file or a contract, not from the engine.
    unique_key: list[str] = Field(default_factory=list)
    produced_by: str | None = None  # path to the SQL file, relative to the repo root
    # Reported by the database; None when the catalog is a hand-written file.
    engine: str | None = None
    total_rows: int | None = None
    total_bytes: int | None = None

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


# --- SQL analysis ------------------------------------------------------------


class OutputColumn(BaseModel):
    """One column produced by a query, and how it is calculated."""

    name: str
    expression: str  # normalized SQL of the calculation
    source_columns: list[str]  # qualified source columns, e.g. "raw.deliveries.actual_time"


class Join(BaseModel):
    kind: str  # e.g. "LEFT", "INNER"
    table: str
    condition: str  # normalized SQL


class SqlAnalysis(BaseModel):
    """The structure of one query, extracted from its syntax tree."""

    target_table: str | None  # None for a plain SELECT
    source_tables: list[str]
    outputs: list[OutputColumn]
    filters: list[str]  # normalized WHERE / HAVING predicates, one per AND-term
    joins: list[Join]
    group_by: list[str]  # normalized GROUP BY expressions
    grain: list[str]  # output columns that identify one row
    limitations: list[str] = Field(default_factory=list)  # what the parser could not resolve


# --- Compatibility -----------------------------------------------------------


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CheckResult(BaseModel):
    name: str  # "columns" | "grain" | "logic"
    status: CheckStatus
    reasons: list[str] = Field(default_factory=list)


class Decision(StrEnum):
    REUSE = "REUSE"
    EVOLVE_SCHEMA = "EVOLVE_SCHEMA"
    NEW_TABLE = "NEW_TABLE"
    NEEDS_INFO = "NEEDS_INFO"


class Candidate(BaseModel):
    """An existing table that might satisfy a proposed table."""

    table: str
    score: float  # used only to rank candidates, never to decide
    matched_columns: list[str]
    shared_sources: list[str]


class CandidateAssessment(BaseModel):
    candidate: Candidate
    checks: list[CheckResult]
    decision: Decision
    new_columns: list[str] = Field(default_factory=list)  # set when decision is EVOLVE_SCHEMA


class ReusePlan(BaseModel):
    """The answer to: does this proposed table already exist?"""

    proposed_table: str | None
    decision: Decision  # the best decision across all candidates
    recommended_table: str | None
    assessments: list[CandidateAssessment]
    limitations: list[str] = Field(default_factory=list)


# --- Lineage -----------------------------------------------------------------


class LineageEdge(BaseModel):
    source: str
    target: str
    columns: list[str] = Field(default_factory=list)  # target columns that depend on source


class Lineage(BaseModel):
    table: str
    upstream: list[LineageEdge]
    downstream: list[LineageEdge]
