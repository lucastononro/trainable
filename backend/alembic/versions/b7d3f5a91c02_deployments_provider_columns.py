"""deployments.provider + provider_endpoint_id

Revision ID: b7d3f5a91c02
Revises: 8e4b2d6f1a35
Create Date: 2026-07-29 19:50:00.000000

Multi-provider compute (PR #145, issue #130): records which compute
provider owns a deployment's endpoint ("modal" | "runpod") plus the
provider-side endpoint id (RunPod endpoint id; unused on Modal). All
existing rows are Modal-era, so `provider` backfills to 'modal'. Replaces
the hand-rolled ALTER TABLEs the PR originally added to the now-retired
`_run_migrations` boot path.

"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7d3f5a91c02"
down_revision: str | None = "8e4b2d6f1a35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column(
            "provider", sa.String(length=32), server_default="modal", nullable=True
        ),
    )
    op.add_column(
        "deployments",
        sa.Column("provider_endpoint_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deployments", "provider_endpoint_id")
    op.drop_column("deployments", "provider")
