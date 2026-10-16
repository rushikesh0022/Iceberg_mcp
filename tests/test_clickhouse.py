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
