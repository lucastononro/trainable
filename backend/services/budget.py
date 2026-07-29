"""Budget guardrail — hard-stop agents when a project's spend exceeds its cap.

The budget is a per-project USD ceiling (`projects.budget_usd`, nullable —
NULL means uncapped). Spend is the sum of `usage_events.cost_usd` across the
whole project (LLM tokens + sandbox compute), so an orchestrator fanning out
to sub-agents — which all share the same session/project — is capped as one
unit.

The agent runner calls `check_budget()`:
  - once before starting a run (a project already over budget never starts
    a new agent), and
  - after every recorded usage event (a running agent halts at its next
    LLM call once the cap is crossed).

`BudgetExceededError` is caught in `run_agent`, which lands the session in a
clean `budget_exceeded` terminal state (not `failed`) with a clear message
telling the user how to raise or clear the cap.

Known limit: usage events whose project resolution failed at insert time
(project_id NULL) don't count toward spend — same blind spot the usage
rollup endpoints already have.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from db import async_session
from models import Experiment, Project
from models import Session as SessionModel
from models import UsageEvent


@dataclass(frozen=True)
class BudgetStatus:
    """Snapshot of a project's budget vs. accumulated spend."""

    project_id: str
    budget_usd: float | None
    spent_usd: float

    @property
    def exceeded(self) -> bool:
        return self.budget_usd is not None and self.spent_usd >= self.budget_usd

    @property
    def remaining_usd(self) -> float | None:
        if self.budget_usd is None:
            return None
        return max(0.0, self.budget_usd - self.spent_usd)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "budget_usd": self.budget_usd,
            "spent_usd": self.spent_usd,
            "remaining_usd": self.remaining_usd,
            "exceeded": self.exceeded,
        }


class BudgetExceededError(Exception):
    """Raised by check_budget when project spend has crossed the cap."""

    def __init__(self, status: BudgetStatus):
        self.status = status
        cap = f"${status.budget_usd:.2f}" if status.budget_usd is not None else "(none)"
        super().__init__(
            f"Project budget exceeded: spent ${status.spent_usd:.4f} of {cap} cap"
        )


async def _project_id_for_session(db, session_id: str) -> str | None:
    """Resolve a session to its project.

    Canonical path mirrors services/usage.py: session → legacy experiment
    back-pointer → project. Falls back to the session's own project_id
    column (backfilled for post-flip sessions with no experiment link).
    """
    row = await db.execute(
        select(Experiment.project_id)
        .join(SessionModel, SessionModel.experiment_id == Experiment.id)
        .where(SessionModel.id == session_id)
    )
    pid = row.scalar_one_or_none()
    if pid:
        return pid
    row = await db.execute(
        select(SessionModel.project_id).where(SessionModel.id == session_id)
    )
    return row.scalar_one_or_none()


async def get_budget_status(
    *, project_id: str | None = None, session_id: str | None = None
) -> BudgetStatus | None:
    """Return the budget status for a project (directly or via a session).

    Returns None when the project can't be resolved (orphan session) or
    doesn't exist — callers treat that as "no budget to enforce".
    """
    async with async_session() as db:
        pid = project_id
        if pid is None and session_id:
            pid = await _project_id_for_session(db, session_id)
        if not pid:
            return None

        row = await db.execute(select(Project.budget_usd).where(Project.id == pid))
        budget = row.scalar_one_or_none()

        row = await db.execute(
            select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
                UsageEvent.project_id == pid
            )
        )
        spent = float(row.scalar_one() or 0.0)

    return BudgetStatus(project_id=pid, budget_usd=budget, spent_usd=spent)


async def check_budget(session_id: str) -> BudgetStatus | None:
    """Raise BudgetExceededError if the session's project is over budget.

    Returns the (non-exceeded) status otherwise — None when the session has
    no resolvable project or the project has no budget set.
    """
    status = await get_budget_status(session_id=session_id)
    if status is not None and status.exceeded:
        raise BudgetExceededError(status)
    return status
