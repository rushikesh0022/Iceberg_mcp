"""Table- and column-level lineage built from the analysed SQL files."""

from __future__ import annotations

from collections import deque

from iceguard.models import Lineage, LineageEdge, SqlAnalysis

DEFAULT_MAX_DEPTH = 5


def build_edges(analyses: dict[str, SqlAnalysis]) -> list[LineageEdge]:
    """One edge per (source -> target), with the target columns that read the source."""
    edges = []
    for target, analysis in sorted(analyses.items()):
        for source in analysis.source_tables:
            columns = [
                o.name
                for o in analysis.outputs
                if any(c.rpartition(".")[0] == source for c in o.source_columns)
            ]
            edges.append(LineageEdge(source=source, target=target, columns=columns))
    return edges


def get_lineage(
    table: str, edges: list[LineageEdge], max_depth: int = DEFAULT_MAX_DEPTH
) -> Lineage:
    return Lineage(
        table=table,
        upstream=_walk(table, edges, upstream=True, max_depth=max_depth),
        downstream=_walk(table, edges, upstream=False, max_depth=max_depth),
    )


def _walk(
    start: str, edges: list[LineageEdge], upstream: bool, max_depth: int
) -> list[LineageEdge]:
    """Breadth-first walk; `seen` stops cycles and `max_depth` stops long chains."""
    found: list[LineageEdge] = []
    seen = {start}
    queue = deque([(start, 0)])
    while queue:
        table, depth = queue.popleft()
        if depth == max_depth:
            continue
        for edge in edges:
            here, there = (edge.target, edge.source) if upstream else (edge.source, edge.target)
            if here != table:
                continue
            found.append(edge)
            if there not in seen:
                seen.add(there)
                queue.append((there, depth + 1))
    return found
