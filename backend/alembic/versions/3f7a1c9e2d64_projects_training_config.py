"""projects.training_config

Revision ID: 3f7a1c9e2d64
Revises: 0bae8e0f765d
Create Date: 2026-07-29 17:55:00.000000

Pre-flight training controls (PR #164, issue #104): JSON blob on `projects`
holding the TrainingConfig (optimization metric, allowed model families,
trial budget, wall-clock/cost caps). Replaces the hand-rolled ALTER TABLE
the PR originally added to the now-retired `_run_migrations` boot path.

"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3f7a1c9e2d64"
down_revision: str | None = "0bae8e0f765d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("training_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "training_config")
