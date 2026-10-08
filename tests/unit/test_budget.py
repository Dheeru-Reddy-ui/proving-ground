"""LLM budgets: the smallest remaining cap wins; a spent day waits, a spent build stops."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pg_core.budget import Budgets, check_budget, next_utc_day

NOW = datetime(2026, 10, 8, 18, 30, tzinfo=UTC)
CAPS = Budgets(
    per_run_usd=Decimal("1.00"), per_build_usd=Decimal("2.00"), per_day_usd=Decimal("5.00")
)


def test_the_run_cap_applies_when_plenty_is_left() -> None:
    check = check_budget(CAPS, build_spent_usd=Decimal(0), day_spent_usd=Decimal(0), now=NOW)
    assert check.allowed
    assert check.run_budget_usd == Decimal("1.00")


def test_the_smallest_remainder_wins() -> None:
    build = check_budget(CAPS, build_spent_usd=Decimal("1.75"), day_spent_usd=Decimal(0), now=NOW)
    assert build.run_budget_usd == Decimal("0.25")
    day = check_budget(CAPS, build_spent_usd=Decimal(0), day_spent_usd=Decimal("4.90"), now=NOW)
    assert day.run_budget_usd == Decimal("0.10")


def test_a_spent_build_budget_is_final() -> None:
    check = check_budget(CAPS, build_spent_usd=Decimal("2.00"), day_spent_usd=Decimal(0), now=NOW)
    assert not check.allowed
    assert check.retry_at is None
    assert check.reason == "build LLM budget used: 2.00 of 2.00 USD"


def test_a_spent_day_waits_for_the_next_utc_day() -> None:
    check = check_budget(CAPS, build_spent_usd=Decimal(0), day_spent_usd=Decimal("5.01"), now=NOW)
    assert not check.allowed
    assert check.retry_at == datetime(2026, 10, 9, tzinfo=UTC)


def test_next_utc_day() -> None:
    assert next_utc_day(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == datetime(
        2027, 1, 1, tzinfo=UTC
    )
