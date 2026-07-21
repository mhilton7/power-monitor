"""Create a corrected status-layout revision for the dashboard information architecture.

Revision ID: 20260721_0007
Revises: 20260720_0006
"""

from __future__ import annotations

from alembic import op

revision = "20260721_0007"
down_revision = "20260720_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical revisions remain immutable. The current configuration is copied into
    # a new system revision while known obsolete placements are repaired in JSON.
    op.execute(
        """
        INSERT INTO status_layout_revisions
            (id, revision, registry_version, configuration, created_by, created_at,
             reason, restored_from_id)
        SELECT
            '00000000-0000-4000-8000-000000000007',
            state.current_revision + 1,
            current.registry_version,
            jsonb_set(
                current.configuration::jsonb,
                '{items}',
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            CASE
                                WHEN item->>'indicator_key' IN
                                    ('system.api_health', 'system.database_health',
                                     'system.worker_health')
                                    THEN item || jsonb_build_object(
                                        'page', 'system_health',
                                        'zone', 'diagnostics_summary'
                                    )
                                WHEN item->>'indicator_key' = 'site.current'
                                    THEN item || jsonb_build_object('visible', false)
                                ELSE item
                            END
                        )
                        FROM jsonb_array_elements(
                            COALESCE(current.configuration::jsonb->'items', '[]'::jsonb)
                        ) AS item
                        WHERE NOT (
                            item->>'indicator_key' IN
                                ('data.current_power', 'data.aggregate_coverage')
                            AND item->>'page' IN ('overview', 'history')
                            AND COALESCE(item->>'role', '*') = '*'
                            AND COALESCE(item->>'breakpoint', 'default') = 'default'
                        )
                    ),
                    '[]'::jsonb
                ) || jsonb_build_array(
                    jsonb_build_object(
                        'indicator_key', 'data.current_power',
                        'page', 'overview', 'role', '*', 'breakpoint', 'default',
                        'visible', false
                    ),
                    jsonb_build_object(
                        'indicator_key', 'data.aggregate_coverage',
                        'page', 'history', 'role', '*', 'breakpoint', 'default',
                        'visible', false
                    )
                ),
                true
            )::json,
            NULL,
            CURRENT_TIMESTAMP,
            'System migration: compact shell, diagnostics relocation, and metric deduplication',
            state.current_revision_id
        FROM status_layout_state AS state
        JOIN status_layout_revisions AS current ON current.id = state.current_revision_id
        WHERE state.id = 'current'
        """
    )
    op.execute(
        """
        UPDATE status_layout_state
        SET current_revision_id = '00000000-0000-4000-8000-000000000007',
            current_revision = current_revision + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 'current'
        """
    )
    op.execute(
        """
        INSERT INTO audit_events
            (id, occurred_at, actor_type, actor_id, action, object_type, object_id,
             source_ip, outcome, correlation_id, details)
        VALUES
            ('00000000-0000-4000-9000-000000000007', CURRENT_TIMESTAMP, 'system', NULL,
             'status_layout.information_architecture_migrated', 'status_layout',
             '00000000-0000-4000-8000-000000000007', NULL, 'success',
             'migration:20260721_0007',
             json_build_object(
                 'summary', 'Moved system health to diagnostics and repaired canonical placements',
                 'previous_revision_preserved', true,
                 'automatic_repair', 'Keep recommended placement'
             ))
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM audit_events WHERE id = '00000000-0000-4000-9000-000000000007'")
    op.execute(
        """
        UPDATE status_layout_state AS state
        SET current_revision_id = migrated.restored_from_id,
            current_revision = previous.revision,
            updated_at = CURRENT_TIMESTAMP
        FROM status_layout_revisions AS migrated
        JOIN status_layout_revisions AS previous ON previous.id = migrated.restored_from_id
        WHERE state.id = 'current'
          AND state.current_revision_id = '00000000-0000-4000-8000-000000000007'
          AND migrated.id = '00000000-0000-4000-8000-000000000007'
        """
    )
    op.execute(
        """
        DELETE FROM status_layout_revisions
        WHERE id = '00000000-0000-4000-8000-000000000007'
          AND NOT EXISTS (
              SELECT 1 FROM status_layout_state
              WHERE current_revision_id = '00000000-0000-4000-8000-000000000007'
          )
        """
    )
