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

- Dashboard: http://localhost:3000 (overview, news, ask, agent network, message log with traces, fault lab)
- Swagger: http://localhost:8008/docs
- Agent Card: http://localhost:8008/card
- Health: http://localhost:8008/health

### Mock agents

Other teams' agents are replaced by three mock agents with made-up data (marked `"mock": true`): two finance agents that
disagree on prices on purpose, and a weather agent. `.env.example` enables them; without a `.env`, run:

```bash
COMPOSE_PROFILES=mock LOCAL_REGISTRY_FILE=local_registry.mock.yaml docker compose up -d --build
```

Inject faults to see retry and failover, then look at the trace of the next answer:

```bash
curl -X POST localhost:9001/fault -H 'content-type: application/json' -d '{"delay_s": 10}'     # mock-finance-a: slow
curl -X POST localhost:9001/fault -H 'content-type: application/json' -d '{"fail_status": 503}' # mock-finance-a: failing
curl -X POST localhost:9001/fault -H 'content-type: application/json' -d '{}'                   # back to normal
curl localhost:9001/stats                                                                       # messages it received
```

Answers are remembered (`stock_price` for 15 minutes), so ask about another ticker to reach the agents again.

### Fault lab (spec §9)

http://localhost:3000/faults needs the admin password (`ADMIN_PASSWORD`, `change-me` by default: change it before
deploying). It switches failures on and off for this agent (slow or failing `/messages`, Gemini, Registry, Identity,
Observability) and for the mock agents. Switches live in Redis, so every replica sees them; they only work with
`FAULTS_ENABLED=true`. Ask a question afterwards and open its trace to see retry, failover and fallbacks.
Container-level failures (crawler, processor) are listed on the page as `docker compose` commands.

`backend/tests/integration/test_faults_spec9.py` has one test per row of the §9 table.

### Gemini API key (optional)

Everything runs without a key, on non-LLM fallbacks:

| Feature | With `GEMINI_API_KEY` | Without |
|---|---|---|
| Search (`mode_used` in the response) | Hybrid: keyword + semantic | Keyword only |
| `news_summary` (`generated_by`) | LLM summary | First sentences of the top articles, confidence 0.3 |
| `sentiment` | LLM | Keyword lexicon (rough) |
| `trending_topics` | Same | Same (never uses the LLM) |
| `/ask` planning (`planned_by`) | LLM | Rules: tickers, weather, trending, topic keywords |
| `/ask` answer (`generated_by`) | LLM | Sentences built from the results |

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
The message format, error codes, retry rules and capability schemas other teams need are in
[`docs/contract.md`](docs/contract.md) (in Vietnamese, for the class).

## Demo: the 7 acceptance scenarios (spec §12)

With the stack running with the mock agents (`COMPOSE_PROFILES=mock`, the default in `.env.example`):

```bash
cd backend
uv run python -m scripts.demo                  # discover, serve, coordinate, learn, conflict, survive, trace
uv run python -m scripts.demo --only 3,7       # some scenarios
uv run python -m scripts.demo --skip-faults    # without scenario 6 (needs FAULTS_ENABLED=true and the admin password)
```

Each scenario prints what happened and checks it; the exit code is non-zero when a check fails. Scenario 6 switches
the §9 faults on one at a time (slow and failing mock agents, Registry, Identity, Observability, Gemini, this agent
itself) and switches them all off at the end. A stopped crawler is the only fault left to try by hand
(`docker compose stop crawler`). Against a deployment, pass `--api`, `--dashboard` and `--password`.

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
  agent/client.py                        # call other agents: discovery, retry, fan-out, failover
  agent/planner.py  coordinator.py       # question → capability calls in parallel → one answer, partial if need be
  agent/capabilities/                    # @capability: news_search, news_summary, sentiment, trending_topics
  api/                                   # /card /health /messages + /api/v1/*
  services/                              # llm (Gemini), search (FTS + pgvector), sentiment, trending, textproc, politeness, ...
  workers/                               # scheduler → crawl.jobs → crawler → news.raw → processor → Postgres ← enricher
  db/                                    # models + repositories;  migrations/ (alembic)
frontend/src/
  app/news/                              # /news (crawl grid, search, results) and /news/[id]
  app/ask/                               # ask a question, see which agents answered
  app/page.tsx  messages/  traces/[id]/  # overview with metrics; message log; trace waterfall
  app/network/  faults/  login/          # agent graph and test messages; fault lab; admin login
  components/  lib/api.ts                # UI pieces; typed server-side client for agent-api
backend/scripts/                         # send (test message), demo (spec §12 scenarios), mock_agent, seed, refetch
docs/contract.md                         # agent contract proposed to the class
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
| 3. Coordination | Mock agents (finance ×2, weather), `AgentClient` (retry, failover), coordinator, planner, `/ask`, knowledge memory | ✅ Done |
| 4. Reliability & observability | Fault injection, spec §9 fault tests, metrics, admin login, `/`, `/messages` + traces, `/network`, `/faults` | ✅ Done |
| 5. Demo ready | Scripted run of the 7 demo scenarios (spec §12), `docs/contract.md` | ✅ Done |
| Later | Remote adapters for Teams 1–4, public deployment, real cross-team runs | — |
