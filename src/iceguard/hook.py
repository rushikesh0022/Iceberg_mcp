"""Step 6a: Claude Code PreToolUse hook that runs the reuse check on every SQL write.

Registered in .claude/settings.json for Write and Edit. For a .sql file under
pipelines/, it computes the file's new content, runs plan_table_reuse, and:

  REUSE, EVOLVE_SCHEMA  -> block the write; the reason tells Claude what to reuse
  NEW_TABLE, NEEDS_INFO -> allow the write; the reasons go to Claude as context

To create the table anyway, add this line to the SQL (the reason is recorded):

  -- iceguard: allow-new-table <why the existing table cannot be used>

IceGuard problems (catalog unreachable, bad input) never block work: the hook
allows the write and says why the check did not run.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from iceguard import runtime, service
from iceguard.models import Decision, ReusePlan
from iceguard.runtime import CatalogUnavailable

WATCHED_DIR = "pipelines"
OVERRIDE = re.compile(r"--\s*iceguard:\s*allow-new-table\b(?P<reason>.*)", re.IGNORECASE)
BLOCKING = {Decision.REUSE, Decision.EVOLVE_SCHEMA}


@dataclass
class HookResult:
    action: str  # "ignore" | "block" | "allow"
    message: str = ""


def evaluate(tool_name: str, tool_input: dict, project_dir: Path) -> HookResult:
    path = _watched_sql_path(tool_input.get("file_path"), project_dir)
    if path is None:
        return HookResult("ignore")

    sql = new_content(tool_name, tool_input, path)
    if sql is None:
        return HookResult("ignore")

    try:
        catalog = runtime.load_catalog()
    except CatalogUnavailable as e:
        return HookResult("allow", f"IceGuard did not check {path.name}: {e}")

    store = runtime.open_store()
    try:
        plan = service.plan_table_reuse(sql, catalog, cache=store)
        override = OVERRIDE.search(sql)
        if plan.decision in BLOCKING and not override:
            result = HookResult("block", _block_message(path, plan))
            action = "blocked"
        elif plan.decision in BLOCKING:
            reason = override.group("reason").strip() or "(no reason given)"
            result = HookResult(
                "allow",
                f"IceGuard: {path.name} would be {plan.decision.value} of "
                f"{plan.recommended_table}, but the override was accepted: {reason}",
            )
            action = "overridden"
        else:
            result = HookResult("allow", _allow_message(path, plan))
            action = "allowed"
        if store:
            store.record_check(
                sql=sql,
                plan=plan,
                source="hook",
                catalog=runtime.catalog_kind(),
                action=action,
                file_path=str(path.relative_to(project_dir)),
            )
        return result
    finally:
        if store:
            store.close()


def new_content(tool_name: str, tool_input: dict, path: Path) -> str | None:
    """The file's content after the tool runs, or None if it cannot be known."""
    if tool_name == "Write":
        return tool_input.get("content")

    current = path.read_text() if path.is_file() else ""
    if tool_name == "Edit":
        edits = [tool_input]
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits", [])
    else:
        return None
    for edit in edits:
        old, new = edit.get("old_string", ""), edit.get("new_string", "")
        if old and old not in current:
            return None  # Claude Code will reject this edit itself
        count = -1 if edit.get("replace_all") else 1
        current = current.replace(old, new, count) if old else new + current
    return current


def _watched_sql_path(file_path: str | None, project_dir: Path) -> Path | None:
    if not file_path or not file_path.endswith(".sql"):
        return None
    path = Path(file_path)
    path = (path if path.is_absolute() else project_dir / path).resolve()
    try:
        relative = path.relative_to(project_dir.resolve())
    except ValueError:
        return None
    return path if relative.parts and relative.parts[0] == WATCHED_DIR else None


def _block_message(path: Path, plan: ReusePlan) -> str:
    best = plan.assessments[0]
    lines = [f"IceGuard blocked {path.name}: {plan.decision.value} {plan.recommended_table}."]
    if plan.decision == Decision.REUSE:
        lines.append(f"{plan.recommended_table} already provides this data. Use it instead.")
    else:
        lines.append(
            f"Add {', '.join(best.new_columns)} to {plan.recommended_table} "
            "(schema change + pipeline change + backfill) instead of creating a new table."
        )
    lines += [f"- {c.name} {c.status.value}: {'; '.join(c.reasons)}" for c in best.checks]
    lines.append(
        "If the existing table truly cannot be used, add the line "
        "'-- iceguard: allow-new-table <reason>' to the SQL and write it again."
    )
    return "\n".join(lines)


def _allow_message(path: Path, plan: ReusePlan) -> str:
    lines = [f"IceGuard checked {path.name}: {plan.decision.value}."]
    for a in plan.assessments[:2]:
        failed = [c for c in a.checks if c.status.value != "PASS"]
        for c in failed:
            lines.append(
                f"- vs {a.candidate.table}, {c.name} {c.status.value}: {'; '.join(c.reasons)}"
            )
    lines += [f"- {limitation}" for limitation in plan.limitations]
    return "\n".join(lines)


def main() -> None:
    try:
        event = json.load(sys.stdin)
        project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".")
        result = evaluate(event.get("tool_name", ""), event.get("tool_input") or {}, project_dir)
    except Exception as e:  # never break the editor because of IceGuard
        result = HookResult("allow", f"IceGuard hook error, check skipped: {e}")
    emit(result)


def emit(result: HookResult) -> None:
    """Write the hook decision for Claude Code (https://code.claude.com/docs/en/hooks).

    Always exit 0 with JSON. "deny" blocks the write and shows the reason to
    Claude. For an allowed write, only additionalContext is set: a
    permissionDecision of "allow" would skip the user's normal permission prompt.
    """
    if result.action == "ignore":
        return
    output: dict = {"hookEventName": "PreToolUse"}
    if result.action == "block":
        output["permissionDecision"] = "deny"
        output["permissionDecisionReason"] = result.message
    else:
        output["additionalContext"] = result.message
    json.dump({"hookSpecificOutput": output}, sys.stdout)


if __name__ == "__main__":
    main()
