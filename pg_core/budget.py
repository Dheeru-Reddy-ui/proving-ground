"""LLM spending limits for the worker (M2.3), as a pure function.

Three caps from config: per generation run, per build, per UTC day. A call is planned only with
the smallest remaining amount. A spent build budget is final for that build; a spent daily budget
only means waiting for the next UTC day.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class Budgets(BaseModel):
    model_config = ConfigDict(frozen=True)

    per_run_usd: Decimal
    per_build_usd: Decimal
    per_day_usd: Decimal


class BudgetCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    run_budget_usd: Decimal = Decimal(0)
    reason: str | None = None
    retry_at: datetime | None = None  # set when waiting can help (the daily cap)


def next_utc_day(now: datetime) -> datetime:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start + timedelta(days=1)


def check_budget(
    budgets: Budgets, *, build_spent_usd: Decimal, day_spent_usd: Decimal, now: datetime
) -> BudgetCheck:
    build_left = budgets.per_build_usd - build_spent_usd
    day_left = budgets.per_day_usd - day_spent_usd
    if build_left <= 0:
        return BudgetCheck(
            allowed=False,
            reason=f"build LLM budget used: {build_spent_usd} of {budgets.per_build_usd} USD",
        )
    if day_left <= 0:
        return BudgetCheck(
            allowed=False,
            reason=f"daily LLM budget used: {day_spent_usd} of {budgets.per_day_usd} USD",
            retry_at=next_utc_day(now),
        )
    return BudgetCheck(allowed=True, run_budget_usd=min(budgets.per_run_usd, build_left, day_left))
