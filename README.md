# News Agent

[![CI](https://github.com/nuroqzavitex/news-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/nuroqzavitex/news-agent/actions/workflows/ci.yml)

A Vietnamese news AI agent for the MI4304 "Multi-Agent Distributed System" course project. The full specification is in [spec.md](spec.md) (Vietnamese).

## Run the whole stack

Requires Docker Desktop.

```bash
cp .env.example .env          # optional; defaults are used when there is no .env
docker compose up -d --build
```

This starts `postgres` (pgvector), `redis`, `migrate` (runs alembic and seeds 6 RSS sources), `agent-api` (port 8008),
`crawler`, `processor`, `scheduler`, `enricher` and `frontend` (port 3000). After about a minute the DB holds a few hundred articles.

- Dashboard: http://localhost:3000/news
- Swagger: http://localhost:8008/docs
- Agent Card: http://localhost:8008/card
- Health: http://localhost:8008/health

### Gemini API key (optional)

Everything runs without a key, on non-LLM fallbacks:

| Feature | With `GEMINI_API_KEY` | Without |
|---|---|---|
| Search (`mode_used` in the response) | Hybrid: keyword + semantic | Keyword only |
| `news_summary` (`generated_by`) | LLM summary | First sentences of the top articles, confidence 0.3 |
| `sentiment` | LLM | Keyword lexicon (rough) |
| `trending_topics` | Same | Same (never uses the LLM) |

To enable it, set `GEMINI_API_KEY` in `.env` and run `docker compose up -d`. The `enricher` then processes existing
articles too, 20 per batch with a 10-second pause (`ENRICH_INTERVAL_S`). `/health` reports `deps.llm`.

## Send a test message (acting as another agent)

```bash
cd backend
uv sync
uv run python -m scripts.send news_search '{"query": "giá vàng", "limit": 3}'
uv run python -m scripts.send news_search '{"query": "gia vang"}' --dup        # dedup: both attempts return the same reply
uv run python -m scripts.send news_search '{"query": "x"}' --bad-token          # 401
uv run python -m scripts.send news_search '{"query": "x"}' --spoof              # 401: token belongs to another agent
uv run python -m scripts.send news_summary '{"query": "giá vàng", "max_words": 80}'
uv run python -m scripts.send sentiment '{"query": "giá vàng", "window": "7d"}'
uv run python -m scripts.send trending_topics '{"window": "24h", "limit": 10}'
```

In local mode, tokens are HMAC-signed with `LOCAL_SHARED_SECRET` (see `app/adapters/local/identity.py`).

## Backend development

```bash
cd backend
uv sync
uv run pytest                                   # unit tests, no Docker needed
docker compose up -d postgres redis             # then:
uv run pytest -m "integration or not integration"   # unit + integration (uses DB news_test, Redis DB 15)
uv run ruff check .
```

Articles stored with only their RSS summary (`status=partial`, e.g. the site was unreachable) can be re-queued with
`uv run python -m scripts.refetch_partial`.

To run the API outside Docker (still using Postgres and Redis from compose):

```bash
docker compose up -d postgres redis migrate
cd backend && uv run uvicorn app.main:app --reload --port 8008
uv run python -m app.workers.scheduler   # other workers run the same way: crawler, processor
```

## Frontend development

```bash
cd frontend
npm install
npm run dev              # http://localhost:3000, talks to agent-api at API_INTERNAL_URL (default http://localhost:8008)
npm run gen:api          # regenerate src/lib/api-types.ts from the running backend's OpenAPI schema
npm run lint && npm run typecheck && npm run build
```

Next.js 16 with Cache Components: every component that loads data at request time calls `connection()` and sits
inside `<Suspense>`, so `next build` never needs the backend.

## Layout

```
backend/app/
  contracts.py  ports.py  factory.py     # shared contract + 4 infrastructure ports + adapter selection
  adapters/local/                        # registry (YAML) · broker (direct HTTP) · identity (HMAC) · tracer (log)
  agent/handler.py  dedup.py             # auth → dedup (Redis) → dispatch (timeout) → persist + trace
  agent/capabilities/                    # @capability: news_search, news_summary, sentiment, trending_topics
  api/                                   # /card /health /messages + /api/v1/*
  services/                              # llm (Gemini), search (FTS + pgvector), sentiment, trending, textproc, politeness, ...
  workers/                               # scheduler → crawl.jobs → crawler → news.raw → processor → Postgres ← enricher
  db/                                    # models + repositories;  migrations/ (alembic)
frontend/src/
  app/news/                              # /news (crawl grid, search, results) and /news/[id]
  components/  lib/api.ts                # UI pieces; typed server-side client for agent-api
docs/adr/                                # architecture decision records
```

## Roadmap

The project is first completed **standalone**: the four shared infrastructure services use the local adapters and other
teams' agents are replaced by mock agents. Connecting to the real class network comes afterwards and only needs
remote adapters.

| Phase | Scope | Status |
|---|---|---|
| 1. Finish M1 | CI, integration tests, Next.js shell, `/news` | ✅ Done |
| 2. AI & knowledge | Gemini embeddings, hybrid search, `news_summary`, `sentiment`, `trending_topics` with non-LLM fallbacks | ✅ Done (Gemini calls not yet tried with a real key) |
| 3. Coordination | Mock agents (finance ×2, weather), `AgentClient` (retry, failover), coordinator, planner, `/ask`, knowledge memory | ⏳ |
| 4. Reliability & observability | Fault injection, spec §9 fault tests, metrics, admin login, `/`, `/messages` + traces, `/network`, `/faults` | ⏳ |
| 5. Demo ready | Scripted run of the 7 demo scenarios (spec §12), `docs/contract.md` | ⏳ |
| Later | Remote adapters for Teams 1–4, public deployment, real cross-team runs | — |
