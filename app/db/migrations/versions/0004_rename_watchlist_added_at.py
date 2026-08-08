"""Rename added_at to created_at in watchlist_items.

Revision ID: 0004_rename_watchlist_added_at
Revises: 0003_fix_sectors
Create Date: 2026-08-08 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision: str = "0004_rename_watchlist_added_at"
down_revision: str | None = "0003_fix_sectors"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Check if added_at exists, and rename it to created_at
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 
                FROM information_schema.columns 
                WHERE table_name='watchlist_items' AND column_name='added_at'
            ) THEN
                ALTER TABLE watchlist_items RENAME COLUMN added_at TO created_at;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    pass
