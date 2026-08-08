"""Initial schema migration — all tables.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-05 00:00:00.000000

Creates the full Atlas schema using raw SQL for maximum compatibility
with Supabase PgBouncer (transaction mode) which does not support
prepared statements. All DDL is idempotent (IF NOT EXISTS).
"""

from __future__ import annotations

from alembic import op

# revision identifiers
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── Extensions ─────────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── Enum types (idempotent) ─────────────────────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE onboarding_status_enum AS ENUM
                ('not_started', 'in_progress', 'completed', 'skipped');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE document_status_enum AS ENUM
                ('uploaded', 'processing', 'ready', 'failed');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE alert_condition_enum AS ENUM
                ('pct_move', 'price_above', 'price_below', 'filing', 'earnings', 'news');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE integration_provider_enum AS ENUM ('google');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE reminder_status_enum AS ENUM ('pending', 'sent', 'cancelled');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)

    # ── users ───────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          SERIAL PRIMARY KEY,
            chat_id     BIGINT NOT NULL,
            username    VARCHAR(100),
            role        VARCHAR(100),
            onboarding_status onboarding_status_enum NOT NULL DEFAULT 'not_started',
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_chat_id ON users (chat_id)")

    # ── user_preferences ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            id                  SERIAL PRIMARY KEY,
            user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            timezone            VARCHAR(50) DEFAULT 'UTC',
            brief_time          VARCHAR(10) DEFAULT '07:00',
            followed_sectors    TEXT[],
            risk_tolerance      VARCHAR(50),
            preferred_currency  VARCHAR(10) DEFAULT 'USD'
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_preferences_user_id ON user_preferences (user_id)")

    # ── watchlist_items ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_items (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol     VARCHAR(20) NOT NULL,
            added_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_watchlist_user_symbol UNIQUE (user_id, symbol)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_watchlist_items_user_id ON watchlist_items (user_id)")

    # ── conversations ───────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            message_count   INTEGER NOT NULL DEFAULT 0,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversations_user_id ON conversations (user_id)")

    # ── messages ────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id              SERIAL PRIMARY KEY,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role            VARCHAR(20) NOT NULL,
            content         TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            token_count     INTEGER
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON messages (conversation_id)")

    # ── conversation_summaries ──────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id                        SERIAL PRIMARY KEY,
            conversation_id           INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            summary_text              TEXT NOT NULL,
            covered_up_to_message_id  INTEGER NOT NULL,
            created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversation_summaries_conversation_id ON conversation_summaries (conversation_id)")

    # ── memory_facts ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_facts (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            fact        TEXT NOT NULL,
            embedding   FLOAT[],
            source      VARCHAR(100),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_memory_facts_user_id ON memory_facts (user_id)")

    # ── research_history ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS research_history (
            id               SERIAL PRIMARY KEY,
            user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            query            TEXT NOT NULL,
            entities         TEXT[],
            response_summary TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_research_history_user_id ON research_history (user_id)")

    # ── documents ───────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id               SERIAL PRIMARY KEY,
            user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            filename         VARCHAR(500) NOT NULL,
            storage_path     VARCHAR(1000) NOT NULL,
            content_type     VARCHAR(100) NOT NULL,
            file_size_bytes  INTEGER NOT NULL,
            status           document_status_enum NOT NULL DEFAULT 'uploaded',
            error_message    TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at     TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_user_id ON documents (user_id)")

    # ── document_chunks (with pgvector) ────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id             SERIAL PRIMARY KEY,
            document_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chunk_index    INTEGER NOT NULL,
            content        TEXT NOT NULL,
            page_number    INTEGER,
            section_title  VARCHAR(500),
            embedding      vector(1536),
            token_count    INTEGER NOT NULL DEFAULT 0
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_document_chunks_document_id ON document_chunks (document_id)")
    # IVFFlat cosine similarity index for RAG retrieval.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding
        ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
    """)
    # GIN full-text search index for BM25-style hybrid retrieval.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_document_chunks_fts
        ON document_chunks USING gin(to_tsvector('english', content))
    """)

    # ── alerts ──────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id                SERIAL PRIMARY KEY,
            user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol            VARCHAR(50) NOT NULL,
            condition         alert_condition_enum NOT NULL,
            threshold         FLOAT,
            description       TEXT,
            is_active         BOOLEAN NOT NULL DEFAULT TRUE,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_triggered_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_alerts_user_id_is_active ON alerts (user_id, is_active)")

    # ── integrations ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS integrations (
            id                       SERIAL PRIMARY KEY,
            user_id                  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider                 integration_provider_enum NOT NULL,
            access_token_encrypted   TEXT NOT NULL,
            refresh_token_encrypted  TEXT,
            scope                    TEXT,
            expires_at               TIMESTAMPTZ,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_integrations_user_id_provider UNIQUE (user_id, provider)
        )
    """)

    # ── notifications_log ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS notifications_log (
            id                SERIAL PRIMARY KEY,
            user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            alert_id          INTEGER REFERENCES alerts(id) ON DELETE SET NULL,
            content_hash      VARCHAR(64) NOT NULL,
            sent_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            notification_type VARCHAR(50) NOT NULL,
            content_preview   VARCHAR(500),
            CONSTRAINT uq_notifications_log_content_hash UNIQUE (content_hash)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_log_user_id ON notifications_log (user_id)")

    # ── reminders ───────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id             SERIAL PRIMARY KEY,
            user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            description    TEXT NOT NULL,
            remind_at      TIMESTAMPTZ NOT NULL,
            related_entity VARCHAR(100),
            status         reminder_status_enum NOT NULL DEFAULT 'pending',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_reminders_user_id ON reminders (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reminders_remind_at ON reminders (remind_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reminders CASCADE")
    op.execute("DROP TABLE IF EXISTS notifications_log CASCADE")
    op.execute("DROP TABLE IF EXISTS integrations CASCADE")
    op.execute("DROP TABLE IF EXISTS alerts CASCADE")
    op.execute("DROP TABLE IF EXISTS document_chunks CASCADE")
    op.execute("DROP TABLE IF EXISTS documents CASCADE")
    op.execute("DROP TABLE IF EXISTS research_history CASCADE")
    op.execute("DROP TABLE IF EXISTS memory_facts CASCADE")
    op.execute("DROP TABLE IF EXISTS conversation_summaries CASCADE")
    op.execute("DROP TABLE IF EXISTS messages CASCADE")
    op.execute("DROP TABLE IF EXISTS conversations CASCADE")
    op.execute("DROP TABLE IF EXISTS watchlist_items CASCADE")
    op.execute("DROP TABLE IF EXISTS user_preferences CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")

    op.execute("DROP TYPE IF EXISTS reminder_status_enum")
    op.execute("DROP TYPE IF EXISTS integration_provider_enum")
    op.execute("DROP TYPE IF EXISTS alert_condition_enum")
    op.execute("DROP TYPE IF EXISTS document_status_enum")
    op.execute("DROP TYPE IF EXISTS onboarding_status_enum")
