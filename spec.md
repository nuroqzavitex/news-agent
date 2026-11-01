# News Agent — Đặc tả dự án (MVP)

> MI4304 Hệ thống phân tán — Bài tập lớn "Multi-Agent Distributed System"
> Phiên bản: 0.2 · Cập nhật: 01/11/2026
>
> **Thay đổi 0.2**: hàng đợi nội bộ dùng **Redis Streams** thay Redpanda; LLM/embedding chốt **Gemini** (vector 768 chiều);
> dashboard MVP rút còn **6 trang**; dự án làm từ đầu (không có bộ khung sẵn).

---

## 1. Tổng quan

**News Agent** là một AI Agent chuyên về **tin tức tiếng Việt**, chạy như một network service độc lập
trong mạng Agent của cả lớp. Agent:

- **Tự thu thập và xử lý** tin từ các báo Việt Nam (RSS), lưu vào DB riêng → đây là *knowledge riêng*
  mà agent khác không có.
- **Cung cấp capability** (tìm tin, tóm tắt, sentiment, xu hướng) cho agent của nhóm khác qua giao thức chung.
- **Gọi agent khác** (Finance, Weather, Travel…) để kết hợp thông tin, và **lưu lại kiến thức học được** kèm provenance.
- **Chịu lỗi**: timeout, retry, duplicate, agent down, hạ tầng down.
- Có **web dashboard** để vận hành, quan sát và demo.

### 1.1 Mục tiêu theo rubric

| Rubric | Tỷ trọng | News Agent đáp ứng bằng |
|---|---|---|
| Domain Agent & knowledge | 15% | Pipeline crawl → xử lý → DB + vector search; capability tin tức |
| Communication & interoperability | 15% | Contract Envelope/Agent Card chung; HTTP/JSON; adapter cho 4 nhóm hạ tầng |
| Discovery & protocol | 10% | Register + heartbeat + `find_by_capability` |
| Knowledge exchange / memory | 10% | Bảng `knowledge_items` có source_agent, version, TTL, confidence |
| Coordination | 15% | `AgentClient` delegate song song tới ≥ 2 agent nhóm khác, gộp kết quả |
| Reliability / fault handling | 15% | Timeout, retry+backoff, dedup, failover, fallback cache, partial result |
| Distributed-system design | 10% | Microservices + Redis Streams (consumer group) nội bộ + API stateless scale ngang |
| Final challenge | 10% | Kịch bản demo §12 |

### 1.2 Nguyên tắc thiết kế

1. **Distributed-system design > LLM intelligence**: ưu tiên protocol, độ tin cậy, khả năng quan sát.
2. **Ports & adapters**: mọi phụ thuộc vào 4 nhóm hạ tầng đi qua interface; có bản local để tự chạy.
3. **Không bao giờ chết vì phụ thuộc**: hạ tầng hoặc agent khác down → agent vẫn trả lời được (degraded).
4. **Idempotent theo `message_id`**: mọi message có thể đến nhiều lần.
5. **Mọi thứ có `trace_id`**: dựng lại được đường đi của mọi request.

---

## 2. Phạm vi MVP

### 2.1 Trong MVP

- **Pipeline dữ liệu**: crawl RSS từ ≥ 5 nguồn, trích nội dung, khử trùng lặp, embedding, sentiment, lưu Postgres.
- **4 capability**: `news_search`, `news_summary`, `sentiment`, `trending_topics`.
- **Agent protocol**: `GET /card`, `GET /health`, `POST /messages` theo contract §6.1.
- **4 adapter hạ tầng** (local + remote): Registry, Broker, Identity, Observability.
- **Coordination**: ít nhất 1 kịch bản gọi ≥ 2 agent nhóm khác.
- **Persistent memory**: lưu kiến thức nhận từ agent khác, có TTL, version và xử lý xung đột.
- **Fault handling**: các tình huống ở §9.
- **Dashboard web** (6 trang): tổng quan, news feed và tìm kiếm, chat với agent, mạng agent, nhật ký message + trace viewer, fault lab.
- **Deploy** bằng Docker Compose lên một máy có địa chỉ public để nhóm khác gọi được.

### 2.2 Ngoài MVP (stretch)

- `event_extraction` (trích sự kiện: ai, ở đâu, khi nào) và `cross_source_verify` (đối chiếu nhiều báo).
- Chạy ≥ 2 replica Agent API sau load balancer, kèm demo kill một replica.
- Pub/sub: chủ động đẩy tin nóng cho agent đã subscribe theo chủ đề.
- Đăng nhập nhiều người dùng, phân quyền dashboard.
- Kubernetes.
- Các trang dashboard: Xu hướng, Console, Memory viewer, Nguồn & crawl (dữ liệu vẫn có qua `/api/v1` và Swagger).

### 2.3 Đối tượng sử dụng

| Đối tượng | Tương tác qua | Nhu cầu |
|---|---|---|
| Agent nhóm khác | `POST /messages` | Gọi capability tin tức |
| Instructor Agent | `POST /messages`, `GET /health` | Kiểm thử, fault injection, hidden tests |
| Thành viên nhóm / người demo | Dashboard web | Xem dữ liệu, gửi task, debug trace, trình bày |

---

## 3. Kiến trúc tổng thể

```
                         ┌──────────── Hạ tầng chung của lớp ─────────────┐
                         │ Team1 Registry · Team2 Broker · Team3 Identity  │
                         │ Team4 Observability                             │
                         └───────────────▲────────────────▲────────────────┘
                                         │ adapters       │
 Agent nhóm khác ──HTTP──▶ ┌─────────────┴────────────────┴──────────────┐
 Instructor Agent          │  agent-api (FastAPI, stateless, N replica)  │◀── Dashboard (Next.js)
                           │  /messages /card /health  +  /api/v1/* (UI) │        │
                           └───┬───────────┬─────────────┬───────────────┘        │
                               │           │             │                        │
                          ┌────▼───┐  ┌────▼────┐   ┌────▼─────┐                  │
                          │Postgres│  │  Redis  │   │ LLM API  │                  │
                          │+pgvector│ │dedup/   │   │ (Gemini) │                  │
                          └────▲───┘  │cache/lock│  └────▲─────┘                  │
                               │      └─────────┘        │                        │
                     ┌─────────┴─────────┐               │                        │
                     │ processor worker  │───────────────┘                        │
                     │ clean·dedup·embed │                                        │
                     │ ·sentiment·store  │                                        │
                     └─────────▲─────────┘                                        │
                               │ Redis Stream news.raw (consumer group)           │
                               │  ← chỉ dùng NỘI BỘ News Agent                    │
                     ┌─────────┴─────────┐                                        │
                     │  crawler worker   │◀── scheduler (mỗi 10 phút/nguồn)       │
                     │  RSS · fetch HTML │                                        │
                     └───────────────────┘                                        │
```

> **Lưu ý:** Redis Streams chỉ dùng cho pipeline **bên trong** News Agent.
> Message giữa các agent đi qua **Broker của Team 2**, hoặc HTTP trực tiếp khi chạy local.

### 3.1 Các service

| Service | Vai trò | Stateful? | Scale |
|---|---|---|---|
| `agent-api` | Agent protocol + REST cho dashboard + gọi agent khác | Không (state ở Postgres/Redis) | Ngang, N replica |
| `crawler` | Đọc RSS, tải HTML, `XADD news.raw` | Không | Ngang (mỗi job crawl lấy lock theo `source_id`) |
| `processor` | `XREADGROUP news.raw` → làm sạch → khử trùng lặp → embedding → sentiment → lưu DB → `XACK` | Không | Ngang theo consumer group |
| `scheduler` | Định kỳ tạo crawl job, dọn memory hết TTL | Không (dùng Redis lock để chỉ 1 instance chạy) | 1 active |
| `frontend` | Dashboard Next.js | Không | — |
| `postgres` | Dữ liệu chính + vector | Có | 1 (stretch: read replica) |
| `redis` | Dedup message, cache, distributed lock, rate limit, **Streams** crawl → process | Có (bật AOF để giữ stream) | 1 |

---

## 4. Tech stack

| Lớp | Công nghệ | Lý do |
|---|---|---|
| Ngôn ngữ backend | Python 3.12 | Hệ sinh thái AI/crawl tốt |
| Web framework | FastAPI + Uvicorn | Async, tự sinh OpenAPI, khung đã có |
| Validation | Pydantic v2 | Contract Envelope/Agent Card |
| HTTP client | httpx (async) | Gọi agent khác và hạ tầng |
| ORM / migration | SQLAlchemy 2.0 (async) + Alembic | Chuẩn, có migration |
| Database | PostgreSQL 16 + **pgvector** | Một DB cho cả quan hệ + vector + full-text |
| Full-text | Postgres FTS (`simple` + `unaccent`) | Tìm tiếng Việt có/không dấu |
| Cache / dedup / lock | Redis 7 (`redis-py` asyncio) | `SET NX EX` cho idempotency, lock scheduler |
| Message queue nội bộ | Redis Streams + consumer group | Có ack, pending list, at-least-once; không thêm container; tách bạch với Broker Team 2 |
| Crawl | `feedparser` + `trafilatura` | Đọc RSS + trích nội dung bài |
| LLM | Gemini (`gemini-2.0-flash`) qua `google-genai`, bọc sau interface `LLMService` | Free tier rộng; đổi provider chỉ sửa 1 file |
| Embedding | Gemini `text-embedding-004`, **cố định 768 chiều** | Đa ngôn ngữ, không cần GPU; đổi model = migration mới |
| Frontend | Next.js 15 (App Router) + TypeScript | SSR, routing sẵn |
| UI | Tailwind CSS + shadcn/ui | Nhanh, đẹp |
| Data fetching | TanStack Query | Cache, polling cho dashboard realtime |
| Biểu đồ | Recharts | Latency, sentiment theo thời gian |
| Graph mạng agent | React Flow | Vẽ topology agent |
| Type FE↔BE | `openapi-typescript` sinh type từ OpenAPI | Không lệch schema |
| Container | Docker + Docker Compose | Chạy cả hệ bằng một lệnh |
| Reverse proxy | Caddy | HTTPS tự động khi deploy public |
| CI | GitHub Actions: ruff, mypy, pytest, eslint, build | Giữ main luôn chạy |
| Test | pytest, pytest-asyncio, httpx ASGITransport; Playwright (stretch) | |

---

## 5. Capability (domain API)

Mọi capability được gọi qua `POST /messages` với `capability` và `payload`.
Response nằm trong `payload` của envelope `type=response`; lỗi thì `type=error`.

### 5.1 `news_search`

```jsonc
// payload request
{ "query": "lãi suất ngân hàng", "limit": 10,
  "from_date": "2026-10-01", "to_date": null,        // tùy chọn
  "sources": ["vnexpress", "cafef"],                  // tùy chọn
  "mode": "hybrid" }                                  // keyword | semantic | hybrid
// payload response
{ "results": [ { "id": "a_123", "title": "...", "snippet": "...", "url": "...",
                 "source": "vnexpress", "published_at": "...", "score": 0.82,
                 "sentiment": "negative" } ],
  "count": 37, "freshness": "2026-11-01T07:50:00+07:00" }  // thời điểm crawl gần nhất
```

### 5.2 `news_summary`

```jsonc
{ "query": "bão số 7", "limit": 8, "max_words": 150 }
→ { "summary": "...", "key_points": ["..."],
    "sources": [ { "id": "a_1", "url": "...", "source": "tuoitre" } ],
    "generated_by": "gemini-2.0-flash", "confidence": 0.7 }
```

### 5.3 `sentiment`

```jsonc
{ "query": "TCB" }   hoặc   { "text": "..." }   hoặc   { "article_ids": ["a_1"] }
→ { "label": "positive|neutral|negative", "score": 0.42,
    "breakdown": { "positive": 12, "neutral": 5, "negative": 3 },
    "window": "7d", "n_articles": 20 }
```

### 5.4 `trending_topics`

```jsonc
{ "window": "24h", "limit": 10, "category": "kinh-te" }
→ { "topics": [ { "topic": "giá vàng", "count": 34, "delta_pct": 120,
                  "sample_article_ids": ["..."] } ] }
```

### 5.5 Stretch

- `event_extraction`: `{article_id}` → `{events: [{who, what, where, when}]}`
- `cross_source_verify`: `{claim}` → `{supporting: [...], conflicting: [...], verdict}`

---

## 6. Agent protocol

### 6.1 Contract (đề xuất cho cả lớp)

**Agent Card**

```json
{ "agent_id": "team-08-news", "group_id": "team-08", "domain": "news",
  "capabilities": ["news_search", "news_summary", "sentiment", "trending_topics"],
  "endpoint": "https://news.team08.example/agent",
  "status": "online", "version": "1.0.0" }
```

**Message Envelope**

```json
{ "message_id": "m-…", "conversation_id": "conv-…", "trace_id": "tr-…",
  "from": "agent-a", "to": "team-08-news",
  "type": "request | response | final | error",
  "capability": "news_search", "reply_to": null,
  "payload": {}, "token": "<Team 3 token>", "timestamp": 1793495337.37 }
```

Quy ước:
- Reply giữ nguyên `conversation_id` và `trace_id`, đặt `reply_to = message_id` gốc.
- Retry **giữ nguyên `message_id`** để bên nhận dedup được.
- `type=error` khi `payload.error` thuộc: `bad_request`, `timeout`, `unavailable`, `internal`.

### 6.2 Endpoint agent

| Method | Path | Mô tả |
|---|---|---|
| GET | `/card` | Agent Card |
| GET | `/health` | `{status, uptime_s, deps: {db, redis, crawler, llm, registry, broker, identity, tracer}}` |
| POST | `/messages` | Nhận Envelope, trả Envelope. 401 nếu token sai, hết hạn hoặc `from` giả |

### 6.3 Luồng xử lý message đến

```
validate token (Team 3) → kiểm tra principal == from → dedup (Redis SET NX theo message_id)
 → nếu trùng: trả reply đã lưu / chờ bản đang xử lý
 → dispatch capability (có timeout) → lưu message + reply vào DB → log trace (Team 4) → trả reply
```

### 6.4 REST cho dashboard (`/api/v1`)

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/v1/articles` | Danh sách / tìm bài (`q`, `source`, `from`, `to`, `page`) |
| GET | `/api/v1/articles/{id}` | Chi tiết bài |
| GET | `/api/v1/sources` · PATCH `/{id}` | Danh sách nguồn, bật/tắt |
| POST | `/api/v1/crawl/trigger` | Kích hoạt crawl thủ công |
| POST | `/api/v1/ask` | Câu hỏi tự nhiên → planner gọi capability nội bộ / agent khác → trả lời kèm `trace_id` |
| POST | `/api/v1/agents/call` | Gửi message thử tới agent bất kỳ (console) |
| GET | `/api/v1/agents` | Agent đã discover được (từ Registry / cache) |
| GET | `/api/v1/messages` | Nhật ký inbox/outbox (`direction`, `status`, `peer`, `capability`) |
| GET | `/api/v1/traces/{trace_id}` | Toàn bộ event của một trace, xếp theo thời gian |
| GET | `/api/v1/memory` · `/memory/{key}/versions` | Knowledge items, lịch sử version, conflict |
| GET | `/api/v1/metrics` | Request/phút, p50/p95 latency, error rate, retry count |
| GET | `/api/v1/infra` | Mode (local/remote) và trạng thái kết nối 4 port |
| POST | `/api/v1/admin/fault` | (dev) bật fault giả lập: delay, drop, trả 503, tắt crawler |

Dashboard đăng nhập bằng một mật khẩu admin trong env (MVP). Endpoint `/api/v1/admin/*` bắt buộc đăng nhập.

---

## 7. Mô hình dữ liệu (PostgreSQL)

```
sources            (id, code, name, rss_url, category, enabled, last_crawled_at, last_status, created_at)

articles           (id, source_id→sources, url UNIQUE, url_hash, title, summary, content,
                    author, category, published_at, crawled_at,
                    simhash BIGINT,                         -- khử bài trùng giữa các báo
                    sentiment_label, sentiment_score,
                    tsv tsvector,                           -- full-text (GIN index)
                    lang, status)                           -- raw|processed|failed

article_chunks     (id, article_id→articles, chunk_index, text,
                    embedding vector(768))                  -- HNSW index (cosine)

topics             (id, name, window_start, window_end, count, delta_pct)   -- tổng hợp định kỳ
article_topics     (article_id, topic_id)

knowledge_items    (id, key, content JSONB,                 -- M4: kiến thức học từ agent khác
                    source_agent, source_message_id,
                    version INT, confidence REAL,
                    created_at, expires_at,                 -- TTL
                    superseded_by→knowledge_items NULL,
                    conflict_group UUID NULL)               -- các bản mâu thuẫn cùng key

messages           (id, message_id UNIQUE, conversation_id, trace_id,
                    direction,                              -- inbound|outbound
                    from_agent, to_agent, type, capability, reply_to,
                    payload JSONB, status,                  -- received|processing|replied|failed|delivered
                    attempts INT, latency_ms, error, created_at, updated_at)

trace_events       (id, trace_id, message_id, agent_id, event, attrs JSONB, ts)   -- bản local; đồng thời đẩy Team 4

agent_cache        (agent_id PK, card JSONB, last_seen_at, source)   -- fallback khi Registry down
```

**Redis keys**

| Key | Dùng cho | TTL |
|---|---|---|
| `dedup:{message_id}` | Trạng thái và reply đã lưu | 10 phút |
| `lock:scheduler` | Leader của scheduler | 30 giây (gia hạn) |
| `cache:cap:{capability}` | Kết quả `find_by_capability` | 60 giây |
| `cache:summary:{hash(query)}` | Kết quả LLM | 30 phút |
| `rate:{agent_id}` | Rate limit theo agent gọi | 60 giây |

**Redis Streams (nội bộ)**

| Stream | Field chính | Producer → Consumer |
|---|---|---|
| `crawl.jobs` | `source_code` | scheduler / API → crawler (group `crawler`) |
| `news.raw` | `source_code`, `url`, `title`, `summary`, `published_at` | crawler → processor (group `processor`) |
| `news.processed` | `article_id` | processor → (stretch) pub/sub, trending |
| `news.dlq` | `url`, `error`, `attempts` | processor (lỗi quá 3 lần) → xem trên dashboard |

Mỗi stream giới hạn `MAXLEN ~ 10000`.

### 7.1 Chính sách memory (M4)

- Mỗi lần nhận dữ liệu từ agent khác mà muốn giữ lại (vd giá cổ phiếu từ Finance) → tạo `knowledge_item`.
- Cùng `key`, cùng `source_agent` → tăng `version`, bản cũ gắn `superseded_by`.
- Cùng `key`, **khác** `source_agent` và nội dung khác → gán chung `conflict_group`. Khi trả lời, ưu tiên bản có `confidence` cao hơn rồi mới hơn, **đồng thời** báo trong response rằng đang có xung đột.
- Bản đã quá `expires_at` → coi là *stale*: chỉ dùng khi không làm mới được, và gắn cờ `stale: true` trong response.
- Scheduler dọn các bản hết hạn quá 7 ngày.

---

## 8. Pipeline dữ liệu

1. **Scheduler** `XADD crawl.jobs` cho mỗi nguồn `enabled` (mỗi 10 phút, key `sched:crawl:{code}` TTL = chu kỳ).
   Lock Redis bảo đảm chỉ một scheduler chạy. `POST /api/v1/crawl/trigger` cũng đẩy job vào stream này.
2. **Crawler** (group `crawler`) đọc RSS, chuẩn hóa URL (bỏ utm/fragment), bỏ qua URL đã có (`url_hash` trong DB
   hoặc `seen:{url_hash}` trong Redis), `XADD news.raw` **chỉ metadata** (url, title, summary, published_at).
3. **Processor** (consumer group `processor`):
   - Kiểm tra `robots.txt` (cache 1 giờ), chờ tới lượt theo host (`rate:host:{host}`, 1 request / 1,5 giây trên toàn hệ),
     tải HTML (timeout 10 giây, retry 2 lần). 429 → để stream giao lại sau; 4xx khác hoặc robots cấm → chỉ lưu tóm tắt RSS (`status=partial`).
   - `trafilatura` trích nội dung, chuẩn hóa unicode.
   - Tính `simhash`; nếu gần trùng bài đã có (Hamming ≤ 3) thì liên kết làm bài trùng, không xử lý lại.
   - Chia chunk khoảng 500 token → embedding theo batch.
   - Sentiment bằng LLM theo batch (fallback: từ điển từ khóa khi LLM lỗi).
   - Ghi DB trong **một transaction**, rồi `XACK` (at-least-once + upsert theo `url` ⇒ idempotent).
   - Message pending quá 60 giây (consumer chết) được consumer khác nhận lại bằng `XAUTOCLAIM`.
   - Lỗi 3 lần (đếm theo delivery count) → `XADD news.dlq` rồi `XACK`.
4. **Trending** (scheduler, mỗi 30 phút): đếm cụm từ khóa và entity trong cửa sổ 24 giờ so với 24 giờ trước → bảng `topics`.

**Nguồn MVP** (RSS công khai): VnExpress, Tuổi Trẻ, Thanh Niên, Dân trí, VietnamNet, CafeF.
Chỉ lưu tiêu đề, tóm tắt, nội dung phục vụ tìm kiếm nội bộ, luôn kèm link gốc; tôn trọng `robots.txt` và giới hạn tần suất.

---

## 9. Độ tin cậy và xử lý lỗi

| Tình huống | Phát hiện | Xử lý | Cách demo |
|---|---|---|---|
| Message trùng (retry) | `dedup:{message_id}` tồn tại | Trả reply đã lưu, không xử lý lại | `send.py --dup`, đếm số lần handler chạy |
| Agent đích không trả lời | Timeout httpx | Retry backoff (0.5 → 1 → 2 giây), giữ `message_id` | Fault: delay 10 giây |
| Agent đích down | ConnectError / 5xx sau retry | Failover sang agent khác cùng capability | Tắt mock agent |
| Không còn agent nào | Danh sách rỗng | Trả **partial result** + `missing: [capability]` | Tắt hết Finance agent |
| Registry down | Lỗi kết nối Team 1 | Dùng `agent_cache` hoặc Redis cache | Đổi `base_url` sai |
| Broker down | Lỗi Team 2 | Fallback gọi HTTP trực tiếp nếu biết endpoint | Tắt broker |
| Identity down | Lỗi Team 3 | Dùng token còn hạn trong cache; validate bằng cache ngắn 60 giây | — |
| Token giả / hết hạn / `from` giả | validate fail | 401, log `msg.rejected` | `send.py --bad-token` |
| Observability down | Lỗi Team 4 | Vẫn ghi `trace_events` local, buffer rồi đẩy lại | — |
| LLM lỗi / chậm | Exception / timeout | Fallback: summary trích xuất câu đầu; sentiment từ điển | Đặt API key sai |
| Crawler chết | `last_crawled_at` quá 30 phút | `status=degraded`, trả dữ liệu cũ kèm `freshness` | Dừng container crawler |
| Processor crash giữa chừng | Message còn trong pending list | `XAUTOCLAIM` giao lại message chưa ack; upsert đảm bảo idempotent | `docker kill processor` |
| Một replica `agent-api` chết (stretch) | Health check LB | LB chuyển sang replica còn lại; dedup ở Redis nên không xử lý trùng | Kill một replica |

**Trạng thái agent** (`/health`, heartbeat):
- `online`: mọi dependency OK.
- `degraded`: crawler/LLM/hạ tầng có lỗi nhưng vẫn trả lời được.
- `offline`: DB không kết nối được.

---

## 10. Observability

- **Trace**: mỗi event có `trace_id`, `message_id`, `agent_id`, `event`, `ts`, `latency_ms`. Ghi vào `trace_events` và đẩy sang Team 4.
- **Event chuẩn**: `msg.received`, `msg.rejected`, `msg.duplicate`, `msg.replied`, `client.discover`, `broker.send`, `broker.retry`, `client.failover`, `registry.fallback_cache`, `llm.call`, `llm.fallback`.
- **Metrics** (tính từ bảng `messages`): request/phút, p50/p95 latency theo capability, error rate, số retry, số duplicate.
- **Log**: JSON lines ra stdout (Docker thu lại).
- **Dashboard**: trang Trace hiển thị timeline dạng waterfall cho một `trace_id`.

---

## 11. Frontend (Dashboard)

**MVP (6 trang)**

| Trang | Route | Nội dung |
|---|---|---|
| Tổng quan | `/` | Trạng thái agent, 4 ô hạ tầng (local/remote, OK/lỗi), metrics chính, biểu đồ latency, số bài crawl 24 giờ, nút crawl ngay |
| Tin tức | `/news` | Feed mới nhất, ô tìm kiếm (keyword/semantic/hybrid), lọc nguồn/ngày/sentiment; trang chi tiết bài |
| Hỏi agent | `/ask` | Chat: nhập câu hỏi → hiện trả lời + các agent đã gọi + link tới trace |
| Mạng agent | `/network` | Graph React Flow: các agent discover được, capability, status; nút "gửi thử" |
| Nhật ký message + Trace | `/messages`, `/traces/[id]` | Bảng inbox/outbox, lọc theo trạng thái/peer/capability; timeline waterfall một trace |
| Fault lab | `/faults` | (dev) Bật/tắt fault giả lập để demo M6 |

**Stretch**: `/trending`, `/console`, `/memory`, `/sources`.

Dashboard polling 3–5 giây bằng TanStack Query (MVP); stretch: SSE cho message/trace realtime.

---

## 12. Kịch bản demo / nghiệm thu MVP

1. **Discover**: News Agent khởi động → register → nhóm khác `find_by_capability("news_search")` thấy `team-08-news`.
2. **Phục vụ**: một agent nhóm khác gửi `news_summary {query: "giá vàng"}` → nhận tóm tắt kèm nguồn trong < 5 giây.
3. **Coordinate**: trên `/ask` hỏi *"Tin tức ảnh hưởng thế nào tới cổ phiếu TCB tuần này?"*. Agent sẽ:
   - lấy tin và sentiment của chính nó;
   - **song song** gọi Finance Agent lấy giá TCB;
   - gộp lại thành câu trả lời, kèm trace cho thấy ≥ 3 agent tham gia.
4. **Learn**: giá TCB được lưu vào `knowledge_items` (source_agent=finance, TTL 15 phút). Hỏi lại trong TTL thì dùng memory, không gọi lại.
5. **Conflict**: hai agent trả giá khác nhau → memory ghi `conflict_group`, câu trả lời nêu rõ có xung đột.
6. **Survive**: bật lần lượt từng fault ở §9, mỗi lần agent vẫn trả lời (có thể degraded/partial) và trace cho thấy retry/failover.
7. **Trace**: mở `/traces/{id}` dựng lại toàn bộ đường đi của kịch bản 3.

**Definition of Done cho MVP**: 7 kịch bản trên chạy được trên môi trường deploy public; CI xanh; README hướng dẫn chạy bằng `docker compose up`.

---

## 13. Cấu trúc thư mục (monorepo)

```
news-agent/
├── README.md
├── spec.md                          # tài liệu này
├── docker-compose.yml               # toàn bộ hệ: api, workers, db, redis, frontend, caddy
├── docker-compose.dev.yml           # override cho dev (hot reload, port debug)
├── .env.example
├── Makefile                         # make up / test / migrate / seed / lint
├── .github/workflows/ci.yml
│
├── docs/
│   ├── contract.md                  # Agent Card + Envelope + mã lỗi — đem đi chốt với lớp
│   ├── architecture.md
│   ├── demo-scenarios.md
│   └── adr/                         # quyết định kiến trúc (vd 001-redis-streams-noi-bo.md)
│
├── backend/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── migrations/versions/
│   ├── config.yaml                  # mode local/remote cho 4 port
│   ├── local_registry.yaml
│   ├── app/
│   │   ├── main.py                  # tạo FastAPI app, lifespan (register, heartbeat)
│   │   ├── settings.py              # Pydantic Settings: env + config.yaml
│   │   ├── contracts.py             # AgentCard, Envelope, Principal
│   │   ├── ports.py                 # 4 interface hạ tầng
│   │   ├── factory.py               # chọn adapter theo config
│   │   ├── adapters/
│   │   │   ├── local/               # registry · broker · identity · tracer
│   │   │   └── remote/              # team1 · team2 · team3 · team4
│   │   ├── agent/
│   │   │   ├── handler.py           # auth → dedup → dispatch → reply
│   │   │   ├── dedup.py             # Redis SET NX
│   │   │   ├── client.py            # discover · send · retry · failover
│   │   │   ├── coordinator.py       # fan-out song song, gộp kết quả, partial result
│   │   │   ├── planner.py           # câu hỏi tự nhiên → các bước capability (LLM)
│   │   │   └── capabilities/
│   │   │       ├── __init__.py      # registry @capability
│   │   │       ├── news_search.py
│   │   │       ├── news_summary.py
│   │   │       ├── sentiment.py
│   │   │       └── trending.py
│   │   ├── api/
│   │   │   ├── agent_routes.py      # /card /health /messages
│   │   │   ├── ui_routes/           # /api/v1/* theo nhóm: articles, agents, messages, traces, memory, admin
│   │   │   └── deps.py              # DI: session, infra, auth admin
│   │   ├── services/
│   │   │   ├── search.py            # hybrid FTS + pgvector (RRF)
│   │   │   ├── llm.py               # Gemini wrapper + fallback
│   │   │   ├── embedding.py
│   │   │   ├── memory.py            # knowledge_items: version, TTL, conflict
│   │   │   ├── metrics.py
│   │   │   └── faults.py            # fault injection (chỉ bật ở dev)
│   │   ├── db/
│   │   │   ├── base.py · session.py
│   │   │   ├── models/              # sources, articles, chunks, knowledge, messages, traces…
│   │   │   └── repositories/
│   │   └── workers/
│   │       ├── crawler.py
│   │       ├── processor.py
│   │       ├── scheduler.py
│   │       └── streams.py           # Redis Streams producer/consumer helper
│   ├── scripts/
│   │   ├── seed_sources.py
│   │   ├── send.py                  # gửi message thử (đóng vai agent khác)
│   │   └── mock_agent.py            # agent giả (Finance/Weather) để test coordination
│   └── tests/
│       ├── unit/
│       ├── integration/             # cần Postgres/Redis (docker compose profile test)
│       └── fault/                   # kịch bản §9
│
└── frontend/
    ├── package.json
    ├── Dockerfile
    ├── next.config.ts
    ├── src/
    │   ├── app/
    │   │   ├── layout.tsx · page.tsx            # tổng quan
    │   │   ├── news/ · news/[id]/
    │   │   ├── ask/ · network/
    │   │   ├── messages/ · traces/[id]/
    │   │   ├── faults/ · login/
    │   ├── components/
    │   │   ├── ui/                  # shadcn
    │   │   ├── trace-waterfall.tsx · agent-graph.tsx · envelope-viewer.tsx
    │   │   └── infra-status.tsx · metrics-cards.tsx
    │   ├── lib/
    │   │   ├── api.ts               # fetch client
    │   │   └── api-types.ts         # sinh từ OpenAPI (npm run gen:api)
    │   └── hooks/
    └── tests/e2e/                   # Playwright (stretch)
```

> Dự án làm từ đầu, không có bộ khung sẵn.

---

## 14. Môi trường và deploy

### 14.1 Biến môi trường (`.env.example`)

```bash
# Agent
AGENT_ID=team-08-news
AGENT_PUBLIC_ENDPOINT=https://news.team08.example
# Hạ tầng (local | remote)
REGISTRY_MODE=local      REGISTRY_URL=
BROKER_MODE=local        BROKER_URL=
IDENTITY_MODE=local      IDENTITY_URL=      IDENTITY_CLIENT_SECRET=     LOCAL_SHARED_SECRET=dev-shared-secret
TRACER_MODE=local        TRACER_URL=
# Data
DATABASE_URL=postgresql+asyncpg://news:news@postgres:5432/news
REDIS_URL=redis://redis:6379/0
# AI
GEMINI_API_KEY=
LLM_MODEL=gemini-2.0-flash
EMBEDDING_MODEL=text-embedding-004   # 768 chiều, khớp vector(768)
# Dashboard
ADMIN_PASSWORD=change-me
NEXT_PUBLIC_API_BASE=http://localhost:8008
# Dev
FAULTS_ENABLED=true
```

### 14.2 Docker Compose services

`postgres` (image `pgvector/pgvector:pg16`), `redis` (bật AOF), `agent-api`, `crawler`, `processor`,
`scheduler`, `frontend`, `caddy` (chỉ khi deploy). Profile `mock` chạy thêm 2 mock agent (Finance, Weather) để tự test coordination.

### 14.3 Deploy để nhóm khác gọi được

- Một VPS (hoặc máy trường) + Caddy cấp HTTPS cho `agent-api` và `frontend`.
- Phương án nhanh: Cloudflare Tunnel từ máy thành viên.
- `AGENT_PUBLIC_ENDPOINT` phải là địa chỉ public, **không** dùng localhost.

---

## 15. Kiểm thử

| Loại | Phạm vi | Công cụ |
|---|---|---|
| Unit | contracts, dedup, memory (version/conflict/TTL), simhash, capability với DB giả | pytest |
| Integration | API + Postgres + Redis thật | docker compose profile `test` |
| Contract | Envelope/Agent Card đúng JSON Schema trong `docs/contract.md` | pytest + jsonschema |
| Multi-agent | News + 2 mock agent in-process / container, coordination + failover | httpx ASGITransport, profile `mock` |
| Fault | Từng dòng bảng §9 | pytest + `/api/v1/admin/fault` |
| Frontend | Build + type-check; e2e các trang chính (stretch) | tsc, Playwright |

CI chạy unit + contract + multi-agent trên mỗi PR; integration chạy trên nhánh `main`.

---

## 16. Kế hoạch và phân công (đề xuất cho 4 người)

| Vai trò | Phụ trách chính |
|---|---|
| **A — Protocol & Infra** | contracts, adapters 4 nhóm hạ tầng, handler/dedup/client/coordinator, làm việc với các nhóm hạ tầng, `docs/contract.md` |
| **B — Data pipeline** | DB schema + migration, crawler, Redis Streams, processor, scheduler, simhash, DLQ |
| **C — AI & Knowledge** | embedding, hybrid search, LLM (summary/sentiment/planner), trending, memory (version/TTL/conflict) |
| **D — Frontend & DevOps** | Dashboard Next.js, Docker Compose, CI, deploy public, trace viewer |

| Giai đoạn | Milestone đề bài | Đầu ra |
|---|---|---|
| Tuần 1–2 | M1 Build | Repo monorepo, compose chạy được, crawler + processor ghi DB, `news_search` keyword, trang `/news` |
| Tuần 3 | M2 Connect | `/messages` với Postgres + Redis dedup, deploy public, contract gửi lớp |
| Tuần 4 | M3 Discover | Adapter Registry (Team 1), heartbeat, trang `/network` |
| Tuần 5–6 | M4 Learn | Embedding + hybrid search, `news_summary`, `sentiment`, memory + trang `/memory` |
| Tuần 7–8 | M5 Coordinate | Coordinator song song, `/ask`, kịch bản với ≥ 2 nhóm thật |
| Tuần 9–10 | M6 Survive | Toàn bộ §9, fault lab, trace viewer, adapter Broker/Identity/Observer thật |
| Tuần 11+ | Final | Stretch (replica, pub/sub), luyện kịch bản §12, báo cáo |

---

## 17. Rủi ro và câu hỏi mở

| # | Rủi ro / câu hỏi | Hướng xử lý |
|---|---|---|
| 1 | Chưa có contract chung của lớp | Chủ động đề xuất `docs/contract.md`; adapter cô lập thay đổi |
| 2 | Nhóm hạ tầng chậm tiến độ | Bản local cho cả 4 port; chuyển từng port khi họ sẵn sàng |
| 3 | Chi phí / quota LLM | Cache Redis, batch, model rẻ (flash/mini), fallback không dùng LLM |
| 4 | Báo chặn crawl / đổi cấu trúc HTML | Ưu tiên RSS, tần suất thấp, `trafilatura` tổng quát, theo dõi `last_status` |
| 5 | Hạ tầng deploy public (VPS / máy trường?) | **Cần chốt** trước tuần 3 |
| 6 | Số hiệu nhóm, `agent_id` chính thức | **Cần chốt** với lớp |
| 7 | Deadline cụ thể từng milestone | **Cần lấy** từ giảng viên, cập nhật §16 |
| 8 | ~~Hàng đợi nội bộ có bị coi là trùng Team 2?~~ | **Đã xử lý (0.2)**: Redis Streams chỉ cho pipeline dữ liệu, ghi trong ADR 001 |
