"""Make an existing sole active sensor the unambiguous Single Home aggregate.

Revision ID: 20260729_0018
Revises: 20260725_0017
"""

from alembic import op

revision = "20260729_0018"
down_revision = "20260725_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE devices
        SET include_in_default_site_total = TRUE
        WHERE lifecycle_status = 'active'
          AND include_in_default_site_total = FALSE
          AND site_id IN (
              SELECT site_id
              FROM devices
              WHERE lifecycle_status = 'active'
              GROUP BY site_id
              HAVING COUNT(*) = 1
          )
        """
    )


def downgrade() -> None:
    # This is a relationship repair, not a schema change. Reverting it would
    # silently remove a user's explicit Home aggregate after they upgraded.
    pass
