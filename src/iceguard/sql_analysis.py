"""Step 2: turn SQL text into a `SqlAnalysis`.

  1. Parse with sqlglot (ClickHouse dialect). CREATE TABLE ... AS SELECT and
     INSERT ... SELECT give the target table; a plain SELECT has none.
  2. qualify() against the catalog schema, so `SELECT *` expands and every
     column is tied to its table; merge_subqueries() inlines simple CTEs.
  3. Rewrite every column as db.table.column (aliases removed), so the same
     calculation written with different aliases produces the same text.
  4. Extract outputs, filters (WHERE / HAVING split on AND), joins, GROUP BY.
  5. Derive the grain (what one row means).

Anything that cannot be resolved goes into `limitations` instead of being guessed.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.merge_subqueries import merge_subqueries
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope

from iceguard.catalog import CatalogSource
from iceguard.models import Join, OutputColumn, SqlAnalysis

DIALECT = "clickhouse"


class SqlAnalysisError(ValueError):
    """The SQL cannot be parsed or resolved against the catalog."""


def analyze_sql(sql: str, catalog: CatalogSource) -> SqlAnalysis:
    statement = _parse_single(sql)
    target = _target_table(statement)
    select = statement.expression if isinstance(statement, (exp.Create, exp.Insert)) else statement

    if not isinstance(select, exp.Select):
        return _unsupported(target, f"Only a single SELECT is supported, got {select.key.upper()}.")

    try:
        select = qualify(
            select.copy(), schema=catalog.schema_mapping(), dialect=DIALECT, identify=False
        )
        select = merge_subqueries(select)
    except OptimizeError as e:
        raise SqlAnalysisError(f"Cannot resolve columns against the catalog: {e}") from e

    scope = build_scope(select)
    if scope is None:
        return _unsupported(target, "Cannot build a query scope.")

    limitations: list[str] = []
    aliases = _alias_map(scope, limitations)
    normalize = _Normalizer(aliases)

    outputs = [
        OutputColumn(
            name=projection.alias_or_name,
            expression=normalize.sql(projection.unalias()),
            source_columns=normalize.source_columns(projection),
        )
        for projection in select.expressions
    ]
    filters = sorted(
        [normalize.sql(p) for p in _conjuncts(select.args.get("where"))]
        + [f"HAVING {normalize.sql(p)}" for p in _conjuncts(select.args.get("having"))]
    )
    joins = [_join(j, normalize, aliases, limitations) for j in select.args.get("joins") or []]
    group = select.args.get("group")
    group_by = [normalize.sql(e) for e in group.expressions] if group else []

    return SqlAnalysis(
        target_table=target,
        source_tables=sorted(set(aliases.values())),
        outputs=outputs,
        filters=filters,
        joins=joins,
        group_by=group_by,
        grain=_grain(select, outputs, group_by, aliases, catalog, limitations),
        limitations=limitations,
    )


# --- Parsing -----------------------------------------------------------------


def _parse_single(sql: str) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except ParseError as e:
        raise SqlAnalysisError(f"Cannot parse SQL: {e}") from e
    if len(statements) != 1:
        raise SqlAnalysisError(f"Expected one SQL statement, found {len(statements)}.")
    return statements[0]


def _target_table(statement: exp.Expression) -> str | None:
    if not isinstance(statement, (exp.Create, exp.Insert)):
        return None
    table = statement.this if isinstance(statement.this, exp.Table) else statement.find(exp.Table)
    return _table_name(table) if table else None


def _table_name(table: exp.Table) -> str:
    return f"{table.db}.{table.name}" if table.db else table.name


def _unsupported(target: str | None, reason: str) -> SqlAnalysis:
    return SqlAnalysis(
        target_table=target,
        source_tables=[],
        outputs=[],
        filters=[],
        joins=[],
        group_by=[],
        grain=[],
        limitations=[reason],
    )


# --- Normalization -----------------------------------------------------------


def _alias_map(scope: Scope, limitations: list[str]) -> dict[str, str]:
    """Map each FROM/JOIN alias to its qualified table name."""
    aliases: dict[str, str] = {}
    for alias, (_, source) in scope.selected_sources.items():
        if isinstance(source, exp.Table):
            aliases[alias] = _table_name(source)
        else:
            limitations.append(
                f"Derived table '{alias}' could not be inlined; its columns are not traced "
                "to source tables."
            )
    return aliases


class _Normalizer:
    def __init__(self, aliases: dict[str, str]) -> None:
        self._aliases = aliases

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Replace table aliases with real table names and order `=` operands."""

        def unalias(n: exp.Expression) -> exp.Expression:
            if isinstance(n, exp.Column) and n.table in self._aliases:
                db, _, table = self._aliases[n.table].rpartition(".")
                return exp.column(n.name, table=table, db=db or None)
            return n

        # Two passes: operands must be unaliased before they are canonicalized.
        return node.copy().transform(unalias).transform(_canonical)

    def sql(self, node: exp.Expression) -> str:
        return self.rewrite(node).sql(DIALECT)

    def source_columns(self, node: exp.Expression) -> list[str]:
        return sorted({c.sql(DIALECT) for c in self.rewrite(node).find_all(exp.Column)})


def _canonical(n: exp.Expression) -> exp.Expression:
    """Write equivalent predicates the same way, so their text can be compared.

    x IN ('A')       -> x = 'A'
    x IN ('B', 'A')  -> x IN ('A', 'B')
    NOT x IN ('A')   -> x <> 'A'
    1 = x            -> 1 = x and x = 1 both become one fixed operand order
    """
    if isinstance(n, exp.Not):
        inner = _canonical(n.this.copy())
        if isinstance(inner, exp.EQ):
            return _canonical(exp.NEQ(this=inner.this, expression=inner.expression))
        return n
    if isinstance(n, exp.In) and n.expressions and not n.args.get("query"):
        values = sorted(n.expressions, key=lambda v: v.sql(DIALECT))
        if len(values) == 1:
            n = exp.EQ(this=n.this, expression=values[0])
        else:
            return exp.In(this=n.this, expressions=values)
    if isinstance(n, (exp.EQ, exp.NEQ)) and n.this.sql(DIALECT) > n.expression.sql(DIALECT):
        return type(n)(this=n.expression, expression=n.this)
    return n


def _conjuncts(clause: exp.Expression | None) -> list[exp.Expression]:
    if clause is None:
        return []
    return list(clause.this.flatten()) if isinstance(clause.this, exp.And) else [clause.this]


def _join(
    join: exp.Join, normalize: _Normalizer, aliases: dict[str, str], limitations: list[str]
) -> Join:
    alias = join.this.alias_or_name
    table = aliases.get(alias, alias)
    on = join.args.get("on")
    if join.args.get("using"):
        limitations.append(f"JOIN ... USING on '{table}' is recorded without a condition.")
    return Join(
        kind=" ".join(p for p in (join.side, join.kind) if p) or "INNER",
        table=table,
        condition=normalize.sql(on) if on else "",
    )


# --- Grain -------------------------------------------------------------------


def _grain(
    select: exp.Select,
    outputs: list[OutputColumn],
    group_by: list[str],
    aliases: dict[str, str],
    catalog: CatalogSource,
    limitations: list[str],
) -> list[str]:
    """Return the output columns that identify one row."""
    by_expression = {o.expression: o.name for o in outputs}

    if group_by:
        missing = [g for g in group_by if g not in by_expression]
        for g in missing:
            limitations.append(f"GROUP BY {g} is not an output column; the grain is incomplete.")
        return [by_expression[g] for g in group_by if g in by_expression]

    if any(_is_aggregate(o) for o in select.expressions):
        limitations.append("Aggregates without GROUP BY: the result is a single row.")
        return []

    if select.args.get("distinct"):
        return [o.name for o in outputs]

    from_ = select.args.get("from_")
    driving = aliases.get(from_.this.alias_or_name) if from_ else None
    if driving is None:
        limitations.append("The FROM source is not a catalog table; the grain is unknown.")
        return []

    table = catalog.get_table(driving)
    if table is None or not table.unique_key:
        limitations.append(f"No unique key is declared for {driving}; the grain is unknown.")
        return []

    for join in select.args.get("joins") or []:
        joined = aliases.get(join.this.alias_or_name)
        if not _is_many_to_one(join, joined, catalog):
            limitations.append(
                f"The join to {joined or join.this.alias_or_name} can multiply rows; "
                "the grain is unknown."
            )
            return []

    grain = []
    for key in table.unique_key:
        qualified = f"{driving}.{key}"
        if qualified not in by_expression:
            limitations.append(
                f"Row key {qualified} is not an output column; the grain is unknown."
            )
            return []
        grain.append(by_expression[qualified])
    return grain


def _is_aggregate(projection: exp.Expression) -> bool:
    return any(not isinstance(agg.parent, exp.Window) for agg in projection.find_all(exp.AggFunc))


def _is_many_to_one(join: exp.Join, joined: str | None, catalog: CatalogSource) -> bool:
    """True when the ON clause pins every unique-key column of the joined table."""
    on = join.args.get("on")
    table = catalog.get_table(joined) if joined else None
    if on is None or table is None or not table.unique_key:
        return False

    alias = join.this.alias_or_name
    pinned: set[str] = set()
    for predicate in _conjuncts(exp.Where(this=on)):
        if not isinstance(predicate, exp.EQ):
            continue
        for side, other in (
            (predicate.this, predicate.expression),
            (predicate.expression, predicate.this),
        ):
            other_refs = {c.table for c in other.find_all(exp.Column)}
            if isinstance(side, exp.Column) and side.table == alias and alias not in other_refs:
                pinned.add(side.name)
    return set(table.unique_key) <= pinned


# --- Whole catalog and upstream expansion --------------------------------------


def analyze_catalog(catalog: CatalogSource) -> tuple[dict[str, SqlAnalysis], list[str]]:
    """Analyse the SQL of every produced table. Return analyses by table, and errors."""
    analyses: dict[str, SqlAnalysis] = {}
    errors: list[str] = []
    for table in catalog.list_tables():
        sql = catalog.read_sql(table)
        if sql is None:
            continue
        try:
            analyses[table.name] = analyze_sql(sql, catalog)
        except SqlAnalysisError as e:
            errors.append(f"{table.name}: {e}")
    return analyses, errors


def expand_upstream(
    analysis: SqlAnalysis, upstream: dict[str, SqlAnalysis], max_depth: int = 5
) -> SqlAnalysis:
    """Rewrite `analysis` in terms of the tables its simple upstream tables read.

    A query that reads analytics.fact_deliveries.delay_minutes becomes a query
    that reads DATE_DIFF(...raw.deliveries...), and it inherits the upstream
    filter. Two queries written at different levels can then be compared.

    Only "simple" upstream tables are inlined: projection and filter, with no
    GROUP BY and no joins. Inlining an aggregate would change the grain, so
    aggregated tables stay as sources. Joined tables are never inlined, because
    their filters would not apply to the driving rows.
    """
    joined = {j.table for j in analysis.joins}
    inline: dict[str, SqlAnalysis] = {}
    if max_depth > 0:
        for source in analysis.source_tables:
            up = upstream.get(source)
            if source not in joined and up is not None and _is_simple(up):
                inline[source] = expand_upstream(up, upstream, max_depth - 1)

    limitations = list(analysis.limitations)

    def substitute(sql: str) -> exp.Expression:
        def visit(n: exp.Expression) -> exp.Expression:
            if not isinstance(n, exp.Column):
                return n
            table = f"{n.db}.{n.table}" if n.db else n.table
            if table not in inline:
                return n
            replacement = next((o for o in inline[table].outputs if o.name == n.name), None)
            if replacement is None:
                limitations.append(f"{table} has no output column {n.name}.")
                return n
            return sqlglot.parse_one(replacement.expression, read=DIALECT)

        node = sqlglot.parse_one(sql, read=DIALECT)
        return node.transform(visit).transform(_canonical)

    def rewrite(sql: str) -> str:
        if sql.startswith("HAVING "):
            return "HAVING " + substitute(sql.removeprefix("HAVING ")).sql(DIALECT)
        return substitute(sql).sql(DIALECT)

    outputs = []
    for o in analysis.outputs:
        node = substitute(o.expression)
        columns = sorted({c.sql(DIALECT) for c in node.find_all(exp.Column)})
        outputs.append(
            OutputColumn(name=o.name, expression=node.sql(DIALECT), source_columns=columns)
        )

    filters = {rewrite(f) for f in analysis.filters}
    for up in inline.values():
        filters.update(up.filters)

    sources = set(analysis.source_tables) - set(inline)
    for up in inline.values():
        sources.update(up.source_tables)

    return analysis.model_copy(
        update={
            "source_tables": sorted(sources),
            "outputs": outputs,
            "filters": sorted(filters),
            "joins": [
                j.model_copy(update={"condition": rewrite(j.condition) if j.condition else ""})
                for j in analysis.joins
            ],
            "group_by": [rewrite(g) for g in analysis.group_by],
            "limitations": limitations,
        }
    )


def identity_analysis(table: str, analysis: SqlAnalysis) -> SqlAnalysis:
    """Describe an existing table as `SELECT <its columns> FROM <it>`.

    Used to compare a proposal that reads an aggregated table directly, which
    expand_upstream() does not inline.
    """
    return SqlAnalysis(
        target_table=table,
        source_tables=[table],
        outputs=[
            OutputColumn(
                name=o.name, expression=f"{table}.{o.name}", source_columns=[f"{table}.{o.name}"]
            )
            for o in analysis.outputs
        ],
        filters=[],
        joins=[],
        group_by=[],
        grain=analysis.grain,
    )


def _is_simple(analysis: SqlAnalysis) -> bool:
    return (
        bool(analysis.grain)
        and not analysis.group_by
        and not analysis.joins
        and not analysis.limitations
    )
