"""Unit tests for each check, using small queries over the demo catalog."""

import pytest

from iceguard.catalog import SchemaFileCatalog
from iceguard.compatibility import check_columns, check_grain, check_logic, decide
from iceguard.models import CheckResult, CheckStatus, Decision
from iceguard.sql_analysis import analyze_catalog, analyze_sql, expand_upstream


@pytest.fixture(scope="module")
def analyses(catalog: SchemaFileCatalog):
    return analyze_catalog(catalog)[0]


@pytest.fixture(scope="module")
def fact(analyses):
    return expand_upstream(analyses["analytics.fact_deliveries"], analyses)


@pytest.fixture
def expand(catalog: SchemaFileCatalog, analyses):
    return lambda sql: expand_upstream(analyze_sql(sql, catalog), analyses)


# --- columns -----------------------------------------------------------------


def test_renamed_column_still_matches(expand, fact) -> None:
    proposed = expand(
        "SELECT delivery_id, dateDiff('minute', promised_time, actual_time) AS lateness "
        "FROM raw.deliveries WHERE status = 'DELIVERED'"
    )
    result = check_columns(proposed, fact)
    assert result.status == CheckStatus.PASS
    assert "lateness: same calculation as delay_minutes (renamed)" in result.reasons


def test_same_name_different_calculation_is_a_conflict(expand, fact) -> None:
    proposed = expand(
        "SELECT delivery_id, dateDiff('second', promised_time, actual_time) AS delay_minutes "
        "FROM raw.deliveries WHERE status = 'DELIVERED'"
    )
    result = check_columns(proposed, fact)
    assert result.status == CheckStatus.FAIL
    assert any("delay_minutes: same name, different calculation" in r for r in result.reasons)


def test_count_does_not_match_across_unrelated_tables(expand, analyses) -> None:
    zombie = expand_upstream(analyses["analytics.zombie_promo_clicks_2024"], analyses)
    proposed = expand("SELECT dasher_id, count() AS n FROM raw.deliveries GROUP BY dasher_id")
    assert check_columns(proposed, zombie).status == CheckStatus.FAIL


# --- grain -------------------------------------------------------------------


def test_unknown_grain(expand, fact) -> None:
    proposed = expand("SELECT status FROM raw.deliveries")
    assert check_grain(proposed, fact).status == CheckStatus.UNKNOWN


# --- logic -------------------------------------------------------------------


def test_extra_filter_on_an_output_column_passes(expand, fact) -> None:
    proposed = expand(
        "SELECT delivery_id FROM raw.deliveries "
        "WHERE status = 'DELIVERED' AND toDate(actual_time) >= '2026-01-01'"
    )
    result = check_logic(proposed, fact)
    assert result.status == CheckStatus.PASS
    assert any("WHERE delivery_date >= '2026-01-01'" in r for r in result.reasons)


def test_extra_filter_on_a_hidden_column_fails(expand, fact) -> None:
    proposed = expand(
        "SELECT delivery_id FROM raw.deliveries WHERE status = 'DELIVERED' AND order_value > 50"
    )
    assert check_logic(proposed, fact).status == CheckStatus.FAIL


def test_fewer_filters_fail(expand, fact) -> None:
    proposed = expand("SELECT delivery_id FROM raw.deliveries")
    assert check_logic(proposed, fact).status == CheckStatus.FAIL


def test_inner_join_fails_because_it_drops_rows(expand, fact) -> None:
    proposed = expand(
        "SELECT d.delivery_id, m.cuisine FROM raw.deliveries AS d "
        "INNER JOIN raw.merchants AS m ON m.merchant_id = d.merchant_id "
        "WHERE d.status = 'DELIVERED'"
    )
    result = check_logic(proposed, fact)
    assert result.status == CheckStatus.FAIL
    assert any("drops rows" in r for r in result.reasons)


# --- decide ------------------------------------------------------------------


def _checks(*statuses: CheckStatus) -> list[CheckResult]:
    return [CheckResult(name=f"c{i}", status=s) for i, s in enumerate(statuses)]


@pytest.mark.parametrize(
    ("statuses", "new", "evolvable", "expected"),
    [
        ((CheckStatus.PASS,) * 3, [], True, Decision.REUSE),
        ((CheckStatus.PASS,) * 3, ["x"], True, Decision.EVOLVE_SCHEMA),
        ((CheckStatus.PASS,) * 3, ["x"], False, Decision.NEW_TABLE),
        ((CheckStatus.PASS, CheckStatus.UNKNOWN, CheckStatus.PASS), [], True, Decision.NEEDS_INFO),
        ((CheckStatus.UNKNOWN, CheckStatus.FAIL, CheckStatus.PASS), [], True, Decision.NEW_TABLE),
    ],
)
def test_decide(statuses, new, evolvable, expected) -> None:
    assert decide(_checks(*statuses), new, evolvable) == expected
