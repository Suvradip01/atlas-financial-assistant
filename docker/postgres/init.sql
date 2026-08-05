-- Atlas PostgreSQL initialization script.
-- Creates required extensions before Alembic runs migrations.
-- This runs once on first container startup.

-- Enable pgvector for vector embeddings (memory_facts, document_chunks).
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable pg_trgm for full-text trigram search (hybrid RAG retrieval).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Enable uuid-ossp for UUID generation (optional convenience).
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
