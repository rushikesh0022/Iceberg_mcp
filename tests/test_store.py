from pathlib import Path

from iceguard.catalog import SchemaFileCatalog
from iceguard.models import Decision
from iceguard.service import plan_table_reuse
from iceguard.sql_analysis import analysis_key, analyze_sql, analyze_sql_cached
from iceguard.store import Store

from .conftest import PROPOSED_DIR, REPO_ROOT

SQL = (PROPOSED_DIR / "01_exact_duplicate.sql").read_text()


def test_cache_round_trip(tmp_path: Path, catalog: SchemaFileCatalog) -> None:
    store = Store(tmp_path / "s.sqlite")
    first = analyze_sql_cached(SQL, catalog, store)
    assert store.get(analysis_key(SQL, catalog)) == first
    assert analyze_sql_cached(SQL, catalog, store) == analyze_sql(SQL, catalog)


def test_cache_key_changes_with_sql_and_catalog(catalog: SchemaFileCatalog, tmp_path: Path) -> None:
    other_schema = tmp_path / "catalog" / "schema.toml"
    other_schema.parent.mkdir()
    text = (REPO_ROOT / "catalog" / "schema.toml").read_text()
    other_schema.write_text(text.replace('unique_key = ["delivery_id"]', "unique_key = []", 1))
    other = SchemaFileCatalog(other_schema, repo_root=REPO_ROOT)

    key = analysis_key(SQL, catalog)
    assert analysis_key(SQL + "\n", catalog) != key
    assert analysis_key(SQL, other) != key


def test_cache_does_not_change_decisions(tmp_path: Path, catalog: SchemaFileCatalog) -> None:
    store = Store(tmp_path / "s.sqlite")
    for path in sorted(PROPOSED_DIR.glob("*.sql")):
        sql = path.read_text()
        assert plan_table_reuse(sql, catalog, store) == plan_table_reuse(sql, catalog)
        assert plan_table_reuse(sql, catalog, store) == plan_table_reuse(sql, catalog)


def test_check_history(tmp_path: Path, catalog: SchemaFileCatalog) -> None:
    store = Store(tmp_path / "s.sqlite")
    plan = plan_table_reuse(SQL, catalog)
    first = store.record_check(sql=SQL, plan=plan, source="hook", catalog="file", action="blocked")
    store.record_check(sql=SQL, plan=plan, source="mcp", catalog="file", action="reported")

    recent = store.recent_checks()
    assert [r.source for r in recent] == ["mcp", "hook"]
    assert recent[1].decision == Decision.REUSE
    assert recent[1].recommended_table == "analytics.fact_deliveries"
    assert store.get_plan(first) == plan
    assert store.action_counts() == {"blocked": 1, "reported": 1}


def test_two_connections_share_the_file(tmp_path: Path, catalog: SchemaFileCatalog) -> None:
    """The MCP server and the hook are separate processes writing one file."""
    path = tmp_path / "s.sqlite"
    plan = plan_table_reuse(SQL, catalog)
    a, b = Store(path), Store(path)
    a.record_check(sql=SQL, plan=plan, source="mcp", catalog="file", action="reported")
    b.record_check(sql=SQL, plan=plan, source="hook", catalog="file", action="blocked")
    assert len(Store(path).recent_checks()) == 2
