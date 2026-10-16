"""The Milestone 1 acceptance tests: each planted proposal gets the right answer."""

import pytest

from iceguard.catalog import SchemaFileCatalog
from iceguard.models import Decision
from iceguard.service import plan_table_reuse

from .conftest import PROPOSED_DIR

CASES = [
    ("01_exact_duplicate.sql", Decision.REUSE, "analytics.fact_deliveries", []),
    ("02_missing_filter.sql", Decision.NEW_TABLE, None, []),
    ("03_different_grain.sql", Decision.NEW_TABLE, None, []),
    (
        "04_evolve_schema.sql",
        Decision.EVOLVE_SCHEMA,
        "analytics.fact_deliveries",
        ["weather_condition"],
    ),
]


@pytest.mark.parametrize(("filename", "decision", "table", "new_columns"), CASES)
def test_planted_case(
    catalog: SchemaFileCatalog,
    filename: str,
    decision: Decision,
    table: str | None,
    new_columns: list[str],
) -> None:
    plan = plan_table_reuse((PROPOSED_DIR / filename).read_text(), catalog)

    assert plan.decision == decision
    assert plan.recommended_table == table
    if new_columns:
        best = next(a for a in plan.assessments if a.candidate.table == table)
        assert best.new_columns == new_columns


def test_missing_filter_reason_names_the_filter(catalog: SchemaFileCatalog) -> None:
    plan = plan_table_reuse((PROPOSED_DIR / "02_missing_filter.sql").read_text(), catalog)
    fact = next(a for a in plan.assessments if a.candidate.table == "analytics.fact_deliveries")
    logic = next(c for c in fact.checks if c.name == "logic")
    assert any("'DELIVERED' = raw.deliveries.status" in r for r in logic.reasons)


def test_different_grain_reports_aggregation_option(catalog: SchemaFileCatalog) -> None:
    plan = plan_table_reuse((PROPOSED_DIR / "03_different_grain.sql").read_text(), catalog)
    fact = next(a for a in plan.assessments if a.candidate.table == "analytics.fact_deliveries")
    grain = next(c for c in fact.checks if c.name == "grain")
    assert "Can be built from analytics.fact_deliveries by aggregation." in grain.reasons
