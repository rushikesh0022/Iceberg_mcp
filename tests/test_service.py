import pytest

from iceguard.catalog import SchemaFileCatalog
from iceguard.models import Decision
from iceguard.service import get_table_lineage, plan_table_reuse, search_catalog

from .conftest import REPO_ROOT

# --- plan_table_reuse ----------------------------------------------------------


def test_reading_an_aggregated_table_directly_is_reuse(catalog: SchemaFileCatalog) -> None:
    plan = plan_table_reuse(
        "SELECT merchant_id, delivery_date, avg_delay_minutes "
        "FROM analytics.agg_merchant_daily_delays",
        catalog,
    )
    assert plan.decision == Decision.REUSE
    assert plan.recommended_table == "analytics.agg_merchant_daily_delays"


def test_a_table_never_matches_itself(catalog: SchemaFileCatalog) -> None:
    sql = (REPO_ROOT / "pipelines" / "existing" / "fact_deliveries.sql").read_text()
    plan = plan_table_reuse(sql, catalog)
    assert all(a.candidate.table != "analytics.fact_deliveries" for a in plan.assessments)


def test_no_related_tables_means_new_table(catalog: SchemaFileCatalog) -> None:
    plan = plan_table_reuse("SELECT merchant_id, cuisine FROM raw.merchants", catalog)
    assert plan.decision == Decision.NEW_TABLE
    assert plan.assessments == []
    assert "No related tables found." in plan.limitations


def test_invalid_sql_is_needs_info(catalog: SchemaFileCatalog) -> None:
    plan = plan_table_reuse("SELECT no_such_column FROM raw.deliveries", catalog)
    assert plan.decision == Decision.NEEDS_INFO
    assert plan.limitations


def test_unsupported_statement_is_needs_info(catalog: SchemaFileCatalog) -> None:
    plan = plan_table_reuse("SELECT 1 UNION ALL SELECT 2", catalog)
    assert plan.decision == Decision.NEEDS_INFO
    assert plan.assessments == []


# --- search_catalog ------------------------------------------------------------


def test_search_finds_delivery_delay_tables(catalog: SchemaFileCatalog) -> None:
    names = [t.name for t in search_catalog("delivery delays", catalog)]
    assert {"analytics.fact_deliveries", "analytics.agg_merchant_daily_delays"} <= set(names[:3])
    assert "analytics.zombie_promo_clicks_2024" not in names


def test_search_with_no_match_is_empty(catalog: SchemaFileCatalog) -> None:
    assert search_catalog("inventory forecast", catalog) == []


# --- get_table_lineage ---------------------------------------------------------


def test_lineage_of_fact_deliveries(catalog: SchemaFileCatalog) -> None:
    lineage = get_table_lineage("analytics.fact_deliveries", catalog)
    assert [(e.source, e.target) for e in lineage.upstream] == [
        ("raw.deliveries", "analytics.fact_deliveries")
    ]
    assert [(e.source, e.target) for e in lineage.downstream] == [
        ("analytics.fact_deliveries", "analytics.agg_merchant_daily_delays")
    ]
    assert "delay_minutes" in lineage.upstream[0].columns


def test_lineage_walks_several_levels(catalog: SchemaFileCatalog) -> None:
    lineage = get_table_lineage("raw.deliveries", catalog)
    assert [e.target for e in lineage.downstream] == [
        "analytics.fact_deliveries",
        "analytics.agg_merchant_daily_delays",
    ]


def test_lineage_depth_limit(catalog: SchemaFileCatalog) -> None:
    lineage = get_table_lineage("raw.deliveries", catalog, max_depth=1)
    assert [e.target for e in lineage.downstream] == ["analytics.fact_deliveries"]


def test_unknown_table_raises(catalog: SchemaFileCatalog) -> None:
    with pytest.raises(ValueError, match="Unknown table"):
        get_table_lineage("analytics.nope", catalog)
