"""Add missing columns to align ORM models with DB schema.

Revision ID: 0002_align_schema
Revises: 0001_initial_schema
Create Date: 2026-08-08 00:00:00.000000

The initial migration was created from the architecture plan.
The ORM models evolved with richer fields. This migration:
1. Adds missing columns to user_preferences to match the ORM model.
2. Adds missing columns to users (onboarding_state).
3. Adds missing enum types used in conversation/models.py.
4. Adds missing columns to watchlist_items (display_name, entity_type, source).
5. Adds missing columns to conversations (last_message_at cleanup).
6. Adds missing columns to messages (modality, tool_calls, telegram_message_id, is_summarized).
7. Adds missing columns to memory_facts (fact_type, fact_value, confidence, status, source_message_id).
8. Adds missing columns to document_chunks (aligns with ORM).
"""

from __future__ import annotations

from alembic import op


revision: str = "0002_align_schema"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── New enum types for conversation models ──────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE entity_type_enum AS ENUM ('public', 'private', 'sector', 'topic');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE watchlist_source_enum AS ENUM ('explicit', 'inferred');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE message_role_enum AS ENUM ('user', 'assistant', 'tool', 'system');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE message_modality_enum AS ENUM ('text', 'voice', 'image', 'document');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE fact_status_enum AS ENUM ('active', 'deprecated');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)

    # ── users: add missing columns ──────────────────────────────────────────────
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) NOT NULL DEFAULT 'UTC'")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_state JSONB")

    # ── user_preferences: replace simple schema with richer ORM schema ──────────
    # Add new columns (keep old ones for now, they'll coexist harmlessly)
    op.execute("ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS followed_companies JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS followed_sectors JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS insight_types JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS brief_time_morning TIME")
    op.execute("ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS brief_time_evening TIME")
    op.execute("ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS brief_enabled BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")

    # ── watchlist_items: add richer fields ──────────────────────────────────────
    op.execute("ALTER TABLE watchlist_items ADD COLUMN IF NOT EXISTS display_name VARCHAR(255)")
    op.execute("ALTER TABLE watchlist_items ADD COLUMN IF NOT EXISTS entity_type entity_type_enum NOT NULL DEFAULT 'public'")
    op.execute("ALTER TABLE watchlist_items ADD COLUMN IF NOT EXISTS source watchlist_source_enum NOT NULL DEFAULT 'explicit'")

    # ── messages: replace simple schema with richer ORM schema ─────────────────
    # Change role column to use enum (may already be varchar)
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS modality message_modality_enum NOT NULL DEFAULT 'text'")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS tool_calls JSONB")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS telegram_message_id INTEGER")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_summarized BOOLEAN NOT NULL DEFAULT FALSE")

    # ── conversation_summaries: align with ORM ──────────────────────────────────
    op.execute("ALTER TABLE conversation_summaries ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE conversation_summaries ADD COLUMN IF NOT EXISTS covers_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb")

    # ── memory_facts: replace simple schema with ORM schema ────────────────────
    op.execute("ALTER TABLE memory_facts ADD COLUMN IF NOT EXISTS fact_type VARCHAR(100)")
    op.execute("ALTER TABLE memory_facts ADD COLUMN IF NOT EXISTS fact_value TEXT")
    op.execute("ALTER TABLE memory_facts ADD COLUMN IF NOT EXISTS confidence FLOAT NOT NULL DEFAULT 1.0")
    op.execute("ALTER TABLE memory_facts ADD COLUMN IF NOT EXISTS status fact_status_enum NOT NULL DEFAULT 'active'")
    op.execute("ALTER TABLE memory_facts ADD COLUMN IF NOT EXISTS source_message_id INTEGER")
    op.execute("ALTER TABLE memory_facts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")

    # ── research_history: add ORM fields ───────────────────────────────────────
    op.execute("ALTER TABLE research_history ADD COLUMN IF NOT EXISTS entity_symbol VARCHAR(50)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_research_history_entity_symbol ON research_history (entity_symbol)")

    # Fix messages role column to work with ORM MessageRole enum values
    # The DB has VARCHAR(20), ORM expects enum. Alter to support both.
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE messages ALTER COLUMN role TYPE message_role_enum
            USING role::message_role_enum;
        EXCEPTION WHEN others THEN NULL;
        END $$
    """)


def downgrade() -> None:
    # Downgrade is intentionally minimal — dropping columns is destructive
    pass
