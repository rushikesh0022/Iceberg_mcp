"""The MCP layer, exercised through a real FastMCP client (in memory)."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from iceguard.mcp_server import mcp

from .conftest import PROPOSED_DIR


def call(tool: str, **arguments):
    async def run():
        async with Client(mcp) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(run())


def test_lists_the_three_read_only_tools() -> None:
    async def run():
        async with Client(mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(run())}
    assert set(tools) == {"search_catalog", "plan_table_reuse", "get_table_lineage"}
    assert all(t.annotations and t.annotations.read_only_hint for t in tools.values())
    assert "BEFORE creating any new table" in tools["plan_table_reuse"].description


@pytest.mark.parametrize(
    ("filename", "decision", "table"),
    [
        ("01_exact_duplicate.sql", "REUSE", "analytics.fact_deliveries"),
        ("02_missing_filter.sql", "NEW_TABLE", None),
        ("03_different_grain.sql", "NEW_TABLE", None),
        ("04_evolve_schema.sql", "EVOLVE_SCHEMA", "analytics.fact_deliveries"),
    ],
)
def test_plan_table_reuse_over_mcp(filename: str, decision: str, table: str | None) -> None:
    result = call("plan_table_reuse", sql=(PROPOSED_DIR / filename).read_text())
    plan = result.structured_content
    assert plan["decision"] == decision
    assert plan["recommended_table"] == table


def test_search_catalog_over_mcp() -> None:
    result = call("search_catalog", query="delivery delays")
    names = [t["name"] for t in result.structured_content["result"]]
    assert "analytics.fact_deliveries" in names


def test_lineage_over_mcp() -> None:
    result = call("get_table_lineage", table="analytics.fact_deliveries")
    downstream = result.structured_content["downstream"]
    assert [e["target"] for e in downstream] == ["analytics.agg_merchant_daily_delays"]


def test_unknown_table_is_a_tool_error() -> None:
    with pytest.raises(ToolError, match="Unknown table"):
        call("get_table_lineage", table="analytics.nope")


def test_missing_schema_file_is_a_tool_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ICEGUARD_SCHEMA", str(tmp_path / "missing.toml"))
    with pytest.raises(ToolError, match="schema file not found"):
        call("search_catalog", query="delivery")


def test_reuse_checks_are_recorded() -> None:
    from iceguard.runtime import open_store

    call("plan_table_reuse", sql=(PROPOSED_DIR / "01_exact_duplicate.sql").read_text())
    [record] = open_store().recent_checks()
    assert (record.source, record.action, record.decision) == ("mcp", "reported", "REUSE")
