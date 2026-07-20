"""Add administrator-managed rate-source metadata.

Revision ID: 20260720_0004
Revises: 20260720_0003
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0004"
down_revision: str | None = "20260720_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rate_sources", sa.Column("effective_from_hint", sa.Date()))
    op.add_column("rate_sources", sa.Column("created_by", sa.String(36)))
    op.create_foreign_key(
        "fk_rate_sources_created_by_users",
        "rate_sources",
        "users",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        "UPDATE rate_sources SET effective_from_hint = DATE '2026-06-01' "
        "WHERE url = "
        "'https://www.sce.com/save-money/rates-financing/residential-rate-plans/"
        "time-of-use-plans'"
    )


def downgrade() -> None:
    op.drop_constraint("fk_rate_sources_created_by_users", "rate_sources", type_="foreignkey")
    op.drop_column("rate_sources", "created_by")
    op.drop_column("rate_sources", "effective_from_hint")
