"""Fix followed_sectors column type from TEXT[] to JSONB.

Revision ID: 0003_fix_sectors
Revises: 0002_align_schema
Create Date: 2026-08-08 00:00:00.000000

The first migration created followed_sectors as TEXT[].
The second migration tried to add it as JSONB IF NOT EXISTS, which skipped it.
This migration explicitly casts the column to JSONB to match the ORM model.
"""

from __future__ import annotations

from alembic import op


revision: str = "0003_fix_sectors"
down_revision: str | None = "0002_align_schema"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Explicitly change column type from TEXT[] to JSONB
    op.execute("""
        ALTER TABLE user_preferences
        ALTER COLUMN followed_sectors TYPE JSONB
        USING to_jsonb(followed_sectors);
    """)
    
    # Also fix default value
    op.execute("""
        ALTER TABLE user_preferences
        ALTER COLUMN followed_sectors SET DEFAULT '[]'::jsonb;
    """)
    
    # Enforce NOT NULL
    op.execute("UPDATE user_preferences SET followed_sectors = '[]'::jsonb WHERE followed_sectors IS NULL;")
    op.execute("ALTER TABLE user_preferences ALTER COLUMN followed_sectors SET NOT NULL;")


def downgrade() -> None:
    pass
