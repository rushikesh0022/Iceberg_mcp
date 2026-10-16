import sqlglot

from iceguard.catalog import SchemaFileCatalog


def test_loads_all_tables(catalog: SchemaFileCatalog) -> None:
    names = {t.name for t in catalog.list_tables()}
    assert {
        "raw.deliveries",
        "raw.weather_hourly",
        "analytics.fact_deliveries",
        "analytics.agg_merchant_daily_delays",
        "analytics.zombie_promo_clicks_2024",
    } <= names


def test_source_tables_have_no_sql(catalog: SchemaFileCatalog) -> None:
    raw = catalog.get_table("raw.deliveries")
    assert raw is not None
    assert catalog.read_sql(raw) is None


def test_every_produced_table_sql_parses_and_creates_that_table(catalog: SchemaFileCatalog) -> None:
    for table in catalog.list_tables():
        sql = catalog.read_sql(table)
        if sql is None:
            continue
        ast = sqlglot.parse_one(sql, read="clickhouse")
        assert ast.this.sql() == table.name, table.produced_by


def test_schema_file_matches_sql_output_columns(catalog: SchemaFileCatalog) -> None:
    for table in catalog.list_tables():
        sql = catalog.read_sql(table)
        if sql is None:
            continue
        select = sqlglot.parse_one(sql, read="clickhouse").expression
        assert select.named_selects == table.column_names(), table.name


def test_schema_mapping_shape(catalog: SchemaFileCatalog) -> None:
    mapping = catalog.schema_mapping()
    assert mapping["raw"]["deliveries"]["delivery_id"] == "UInt64"
