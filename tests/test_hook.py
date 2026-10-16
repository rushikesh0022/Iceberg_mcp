import io
import json
import os
from pathlib import Path

import pytest

from iceguard import hook
from iceguard.runtime import open_store

from .conftest import PROPOSED_DIR, REPO_ROOT

DUPLICATE = (PROPOSED_DIR / "01_exact_duplicate.sql").read_text()
MISSING_FILTER = (PROPOSED_DIR / "02_missing_filter.sql").read_text()
EVOLVE = (PROPOSED_DIR / "04_evolve_schema.sql").read_text()


def run(tool_name: str, tool_input: dict, monkeypatch) -> dict | None:
    """Run the hook the way Claude Code does: JSON on stdin, JSON on stdout."""
    event = {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input}
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(REPO_ROOT))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    hook.main()
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


def write(name: str, content: str) -> dict:
    return {"file_path": str(REPO_ROOT / "pipelines" / "new" / name), "content": content}


def test_duplicate_is_blocked(monkeypatch) -> None:
    out = run("Write", write("delays.sql", DUPLICATE), monkeypatch)
    assert out["permissionDecision"] == "deny"
    assert "REUSE analytics.fact_deliveries" in out["permissionDecisionReason"]
    assert "allow-new-table" in out["permissionDecisionReason"]


def test_evolve_schema_is_blocked_with_the_columns_to_add(monkeypatch) -> None:
    out = run("Write", write("weather.sql", EVOLVE), monkeypatch)
    assert out["permissionDecision"] == "deny"
    assert "Add weather_condition to analytics.fact_deliveries" in out["permissionDecisionReason"]


def test_new_table_is_allowed_with_context_and_no_auto_approval(monkeypatch) -> None:
    out = run("Write", write("all_status.sql", MISSING_FILTER), monkeypatch)
    assert "permissionDecision" not in out  # the user's permission prompt still applies
    assert "NEW_TABLE" in out["additionalContext"]
    assert "'DELIVERED' = raw.deliveries.status" in out["additionalContext"]


def test_override_allows_and_is_recorded(monkeypatch) -> None:
    sql = DUPLICATE + "\n-- iceguard: allow-new-table separate retention policy\n"
    out = run("Write", write("delays.sql", sql), monkeypatch)
    assert "permissionDecision" not in out
    assert "override was accepted: separate retention policy" in out["additionalContext"]
    assert open_store().recent_checks()[0].action == "overridden"


@pytest.mark.parametrize(
    "path", ["README.md", "seed/new.sql", "/tmp/elsewhere/pipelines/x.sql", "pipelines/notes.txt"]
)
def test_files_outside_pipelines_are_ignored(monkeypatch, path: str) -> None:
    file_path = path if os.path.isabs(path) else str(REPO_ROOT / path)
    assert run("Write", {"file_path": file_path, "content": DUPLICATE}, monkeypatch) is None


def test_edit_is_checked_on_the_resulting_file(monkeypatch, tmp_path: Path) -> None:
    """Editing case 2 to add the DELIVERED filter turns it into a duplicate."""
    target = REPO_ROOT / "pipelines" / "proposed" / "02_missing_filter.sql"
    edit = {
        "file_path": str(target),
        "old_string": "FROM raw.deliveries AS d;",
        "new_string": "FROM raw.deliveries AS d\nWHERE d.status = 'DELIVERED';",
    }
    out = run("Edit", edit, monkeypatch)
    assert out["permissionDecision"] == "deny"
    assert "REUSE analytics.fact_deliveries" in out["permissionDecisionReason"]


def test_new_content_for_edit_tools(tmp_path: Path) -> None:
    f = tmp_path / "a.sql"
    f.write_text("a b a")
    assert hook.new_content("Edit", {"old_string": "a", "new_string": "x"}, f) == "x b a"
    edit_all = {"old_string": "a", "new_string": "x", "replace_all": True}
    assert hook.new_content("Edit", edit_all, f) == "x b x"
    edits = {
        "edits": [{"old_string": "a", "new_string": "x"}, {"old_string": "b", "new_string": "y"}]
    }
    assert hook.new_content("MultiEdit", edits, f) == "x y a"
    assert hook.new_content("Edit", {"old_string": "zzz", "new_string": "x"}, f) is None


def test_checks_are_recorded(monkeypatch) -> None:
    run("Write", write("delays.sql", DUPLICATE), monkeypatch)
    run("Write", write("all_status.sql", MISSING_FILTER), monkeypatch)
    records = open_store().recent_checks()
    assert [(r.action, r.file_path) for r in records] == [
        ("allowed", "pipelines/new/all_status.sql"),
        ("blocked", "pipelines/new/delays.sql"),
    ]


def test_unavailable_catalog_never_blocks(monkeypatch) -> None:
    monkeypatch.setenv("ICEGUARD_CATALOG", "clickhouse")
    monkeypatch.setenv("ICEGUARD_CH_PORT", "1")
    out = run("Write", write("delays.sql", DUPLICATE), monkeypatch)
    assert "permissionDecision" not in out
    assert "did not check" in out["additionalContext"]


def test_bad_input_never_blocks(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    hook.main()
    assert "check skipped" in json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
