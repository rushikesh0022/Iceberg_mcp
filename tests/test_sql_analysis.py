from pathlib import Path

import pytest

from iceguard.catalog import SchemaFileCatalog
from iceguard.sql_analysis import SqlAnalysisError, analyze_sql

from .conftest import PROPOSED_DIR, REPO_ROOT

EXISTING_DIR = REPO_ROOT / "pipelines" / "existing"

DELAY = "DATE_DIFF(MINUTE, raw.deliveries.promised_time, raw.deliveries.actual_time)"
DELIVERY_DATE = "toDate(raw.deliveries.actual_time)"
DELIVERED = "'DELIVERED' = raw.deliveries.status"


def analyze(path: Path, catalog: SchemaFileCatalog):
    return analyze_sql(path.read_text(), catalog)


def outputs(analysis) -> dict[str, str]:
    return {o.name: o.expression for o in analysis.outputs}


# --- The planted files -------------------------------------------------------


def test_fact_deliveries(catalog: SchemaFileCatalog) -> None:
    a = analyze(EXISTING_DIR / "fact_deliveries.sql", catalog)

    assert a.target_table == "analytics.fact_deliveries"
    assert a.source_tables == ["raw.deliveries"]
    assert outputs(a)["delay_minutes"] == DELAY
    assert outputs(a)["delivery_date"] == DELIVERY_DATE
    assert a.filters == [DELIVERED]
    assert a.joins == []
    assert a.grain == ["delivery_id"]
    assert a.limitations == []


def test_source_columns_are_traced(catalog: SchemaFileCatalog) -> None:
    a = analyze(EXISTING_DIR / "fact_deliveries.sql", catalog)
    delay = next(o for o in a.outputs if o.name == "delay_minutes")
    assert delay.source_columns == ["raw.deliveries.actual_time", "raw.deliveries.promised_time"]


def test_aliases_do_not_change_expressions(catalog: SchemaFileCatalog) -> None:
    """Case 1 uses alias `del` and another column order; the logic text must match."""
    existing = analyze(EXISTING_DIR / "fact_deliveries.sql", catalog)
    proposed = analyze(PROPOSED_DIR / "01_exact_duplicate.sql", catalog)

    for name, expression in outputs(proposed).items():
        assert outputs(existing)[name] == expression
    assert proposed.filters == existing.filters
    assert proposed.grain == existing.grain


def test_missing_filter_is_visible(catalog: SchemaFileCatalog) -> None:
    a = analyze(PROPOSED_DIR / "02_missing_filter.sql", catalog)
    assert a.filters == []
    assert a.grain == ["delivery_id"]


def test_group_by_sets_the_grain(catalog: SchemaFileCatalog) -> None:
    a = analyze(PROPOSED_DIR / "03_different_grain.sql", catalog)
    assert a.group_by == [
        "analytics.fact_deliveries.dasher_id",
        "analytics.fact_deliveries.delivery_date",
    ]
    assert a.grain == ["dasher_id", "delivery_date"]


def test_group_by_on_an_output_alias_is_resolved(catalog: SchemaFileCatalog) -> None:
    """zombie_promo_clicks_2024 groups by `click_date`, an alias of toDate(clicked_at)."""
    a = analyze(EXISTING_DIR / "zombie_promo_clicks_2024.sql", catalog)
    assert a.grain == ["promo_id", "click_date"]


def test_many_to_one_left_join_keeps_the_grain(catalog: SchemaFileCatalog) -> None:
    a = analyze(PROPOSED_DIR / "04_evolve_schema.sql", catalog)

    assert [(j.kind, j.table) for j in a.joins] == [("LEFT", "raw.weather_hourly")]
    assert a.grain == ["delivery_id"]
    assert a.filters == [DELIVERED]
    assert outputs(a)["weather_condition"] == "raw.weather_hourly.weather_condition"


@pytest.mark.parametrize("path", sorted((REPO_ROOT / "pipelines").rglob("*.sql")), ids=str)
def test_every_planted_file_is_fully_resolved(catalog: SchemaFileCatalog, path: Path) -> None:
    a = analyze(path, catalog)
    assert a.limitations == []
    assert a.grain


# --- Edge cases --------------------------------------------------------------


def test_join_that_can_multiply_rows_makes_grain_unknown(catalog: SchemaFileCatalog) -> None:
    a = analyze_sql(
        "SELECT d.delivery_id, w.weather_condition FROM raw.deliveries AS d "
        "LEFT JOIN raw.weather_hourly AS w ON w.zone_id = d.zone_id",
        catalog,
    )
    assert a.grain == []
    assert any("multiply rows" in lim for lim in a.limitations)


def test_cte_is_inlined_and_select_star_expanded(catalog: SchemaFileCatalog) -> None:
    a = analyze_sql(
        "WITH x AS (SELECT * FROM raw.deliveries WHERE status = 'DELIVERED') "
        "SELECT delivery_id, toDate(actual_time) AS delivery_date FROM x",
        catalog,
    )
    assert a.source_tables == ["raw.deliveries"]
    assert a.filters == [DELIVERED]
    assert outputs(a)["delivery_date"] == DELIVERY_DATE
    assert a.grain == ["delivery_id"]


def test_plain_select_and_insert_targets(catalog: SchemaFileCatalog) -> None:
    select = "SELECT delivery_id FROM raw.deliveries"
    assert analyze_sql(select, catalog).target_table is None
    assert analyze_sql(f"INSERT INTO analytics.x {select}", catalog).target_table == "analytics.x"


def test_aggregate_without_group_by_is_one_row(catalog: SchemaFileCatalog) -> None:
    a = analyze_sql("SELECT count() FROM raw.deliveries", catalog)
    assert a.grain == []
    assert a.limitations == ["Aggregates without GROUP BY: the result is a single row."]


def test_missing_row_key_makes_grain_unknown(catalog: SchemaFileCatalog) -> None:
    a = analyze_sql("SELECT status FROM raw.deliveries", catalog)
    assert a.grain == []
    assert any("raw.deliveries.delivery_id" in lim for lim in a.limitations)


def test_union_is_reported_as_unsupported(catalog: SchemaFileCatalog) -> None:
    a = analyze_sql("SELECT 1 UNION ALL SELECT 2", catalog)
    assert a.outputs == []
    assert a.limitations == ["Only a single SELECT is supported, got UNION."]


@pytest.mark.parametrize(
    "sql",
    [
        "SELEC delivery_id FROM raw.deliveries",
        "SELECT 1; SELECT 2",
        "SELECT no_such_column FROM raw.deliveries",
    ],
)
def test_invalid_sql_raises(catalog: SchemaFileCatalog, sql: str) -> None:
    with pytest.raises(SqlAnalysisError):
        analyze_sql(sql, catalog)
