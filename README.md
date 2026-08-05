# Atlas — AI Financial Assistant for Telegram

Atlas is an elite AI financial analyst that lives in Telegram. It is designed to provide proactive intelligence, document analysis, live market data, and Google Workspace-powered meeting prep.

Built entirely on the **v2 Architecture** (Layered modular monolith, LangGraph orchestration, async FastAPI, PostgreSQL + pgvector, and Redis), Atlas delivers production-grade resilience, latency-aware state management, and high extensibility.

## Features

- **Proactive Intelligence:** Daily market briefs, price alerts, and SEC filing monitors running unprompted via Arq background jobs.
- **Deep Document Analysis:** Full 6-stage RAG pipeline with hybrid retrieval, reranking, and citation validation against user-uploaded PDFs and documents.
- **Google Workspace Integration:** Self-hosted Model Context Protocol (MCP) server for OAuth-secured integration with Gmail, Calendar, Drive, and Sheets.
- **Real-time Market Data:** Finnhub and SEC EDGAR integrations for live prices, financials, and company research.
- **Voice Support:** Audio transcription powered by OpenAI Whisper for on-the-go queries.
- **Progress Streaming:** Telegram typing indicators and throttled message edits for real-time AI reasoning transparency.

## Architecture

1. **FastAPI App:** Handles Telegram webhooks, Google OAuth callbacks, and system lifecycle.
2. **Workflow Router:** Directs incoming Telegram messages to 1 of 4 LangGraph workflows (Onboarding, Conversation, Document QA, Meeting Prep).
3. **Agent Registry:** Contains 6 single-responsibility agents.
4. **Tool Layer:** Wraps APIs (Finnhub, SEC, Tavily, Workspace) into predictable schemas.
5. **Worker Pool (Arq):** Handles scheduled pipelines (Morning Brief, Watchlist Monitor) and event-driven ingestion (Document Parsing).
6. **Persistence:** PostgreSQL (with pgvector and pg_trgm) for the system of record, Redis for deduplication and background jobs.

## Deployment Instructions

Atlas is designed to be deployed via Docker Compose on a persistent host (e.g., AWS EC2, DigitalOcean Droplet) with Nginx providing TLS termination.

### Prerequisites
- Docker and Docker Compose installed.
- A Telegram Bot Token from [@BotFather](https://core.telegram.org/bots#botfather).
- API Keys: OpenAI, Finnhub, Tavily (and optionally AWS/S3 keys).
- SSL Certificates (e.g., from Let's Encrypt / Certbot).

### Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-org/atlas-financial-assistant.git
   cd atlas-financial-assistant
   ```

2. **Configure Environment:**
   Copy the example config and fill in your secrets.
   ```bash
   cp .env.example .env
   ```
   **Important Keys to Fill:**
   - `APP_SECRET_KEY`, `OAUTH_STATE_SECRET`, `TOKEN_ENCRYPTION_KEY`
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_WEBHOOK_URL`
   - `OPENAI_API_KEY`, `FINNHUB_API_KEY`, `TAVILY_API_KEY`
   - `DATABASE_URL` and `REDIS_URL`

3. **Provision SSL Certificates:**
   Place your SSL certificates inside `docker/nginx/ssl/`:
   ```bash
   mkdir -p docker/nginx/ssl
   cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem docker/nginx/ssl/
   cp /etc/letsencrypt/live/yourdomain.com/privkey.pem docker/nginx/ssl/
   ```

4. **Initialize the Database:**
   Run the Alembic migrations to set up pgvector and the database schema.
   ```bash
   docker-compose run --rm app alembic upgrade head
   ```

5. **Start the System:**
   ```bash
   docker-compose up -d
   ```
   This will start:
   - `app` (FastAPI webhook and API)
   - `worker` (Arq background jobs scheduler and executor)
   - `postgres` (Database)
   - `redis` (Cache/Broker)
   - `mcp-google-workspace` (Local MCP server)
   - `nginx` (Reverse Proxy)

6. **Verify the Webhook:**
   Check the `app` container logs to ensure the Telegram webhook was successfully registered on startup:
   ```bash
   docker-compose logs -f app
   ```

## Local Development

For local development without Docker:
1. `pip install -r requirements.txt`
2. `alembic upgrade head`
3. `uvicorn app.main:app --reload`
4. Use `ngrok http 8000` to expose your local server and update `TELEGRAM_WEBHOOK_URL` in `.env`.

## Code Quality

This project enforces:
- Clean Architecture (6-layer call chain)
- Strict type hints (`mypy` compliant)
- PEP 8 (via `ruff`)
- Async I/O for all blocking operations
