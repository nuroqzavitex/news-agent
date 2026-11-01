# News Agent

A Vietnamese news AI agent for the MI4304 "Multi-Agent Distributed System" course project. The full specification is in [spec.md](spec.md) (Vietnamese).

## Run the whole stack

Requires Docker Desktop.

```bash
cp .env.example .env          # optional; defaults are used when there is no .env
docker compose up -d --build
```

This starts `postgres` (pgvector), `redis`, `migrate` (runs alembic and seeds 6 RSS sources), `agent-api` (port 8008),
`crawler`, `processor` and `scheduler`. After about a minute the DB holds a few hundred articles.

- Swagger: http://localhost:8008/docs
- Agent Card: http://localhost:8008/card
- Health: http://localhost:8008/health

## Send a test message (acting as another agent)

```bash
cd backend
uv sync
uv run python -m scripts.send news_search '{"query": "giá vàng", "limit": 3}'
uv run python -m scripts.send news_search '{"query": "gia vang"}' --dup        # dedup: both attempts return the same reply
uv run python -m scripts.send news_search '{"query": "x"}' --bad-token          # 401
uv run python -m scripts.send news_search '{"query": "x"}' --spoof              # 401: token belongs to another agent
```

In local mode, tokens are HMAC-signed with `LOCAL_SHARED_SECRET` (see `app/adapters/local/identity.py`).

## Backend development

```bash
cd backend
uv sync
uv run pytest            # unit tests, no Docker needed
uv run ruff check .
```

To run the API outside Docker (still using Postgres and Redis from compose):

```bash
docker compose up -d postgres redis migrate
cd backend && uv run uvicorn app.main:app --reload --port 8008
uv run python -m app.workers.scheduler   # other workers run the same way: crawler, processor
```

## Layout

```
backend/app/
  contracts.py  ports.py  factory.py     # shared contract + 4 infrastructure ports + adapter selection
  adapters/local/                        # registry (YAML) · broker (direct HTTP) · identity (HMAC) · tracer (log)
  agent/handler.py  dedup.py             # auth → dedup (Redis) → dispatch (timeout) → persist + trace
  agent/capabilities/                    # @capability: news_search
  api/                                   # /card /health /messages + /api/v1/*
  services/                              # search (FTS), textproc (simhash), politeness, health, tracing
  workers/                               # scheduler → crawl.jobs → crawler → news.raw → processor → Postgres
  db/                                    # models + repositories;  migrations/ (alembic)
docs/adr/                                # architecture decision records
```

## Progress

| Milestone | Status |
|---|---|
| M1 Build | ✅ Backend: compose, crawl → DB pipeline, `news_search` (keyword, with/without diacritics), agent protocol + dedup + local auth. ⏳ Frontend `/news` |
| M2 Connect | ⏳ Public deployment, share `docs/contract.md` with the class |
| M3–M6 | Not started (see spec §16) |
