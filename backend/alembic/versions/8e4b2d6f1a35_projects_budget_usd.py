"""projects.budget_usd

Revision ID: 8e4b2d6f1a35
Revises: 3f7a1c9e2d64
Create Date: 2026-07-29 18:05:00.000000

Per-project cost budget (PR #165, issue #107): hard-stop spend cap in USD
across the whole project, enforced by services/budget.py via the agent
runner. NULL = uncapped. Replaces the hand-rolled ALTER TABLE the PR
originally added to the now-retired `_run_migrations` boot path.

"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8e4b2d6f1a35"
down_revision: str | None = "3f7a1c9e2d64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("budget_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "budget_usd")
