# Contract giữa các agent: News Agent (team-08)

Bản đề xuất để chốt với cả lớp. Tài liệu ghi đúng những gì `team-08-news` đang làm trong code. Khi các nhóm
thống nhất điểm nào khác đi, nhóm mình sẽ sửa code theo và cập nhật tài liệu này.

- Mục 1–5 là phần **mọi agent** nên theo, để agent nào cũng gọi được agent nào.
- Mục 6–7 là capability News Agent **cung cấp** và **cần dùng** của nhóm khác.
- Mục 8 là những gì nhóm mình cần từ các nhóm hạ tầng (Team 1–4).
- Mục 9 là các câu hỏi còn mở.

## 1. Agent Card

Mỗi agent đăng ký với Registry (Team 1) bằng một Agent Card. Card cũng trả ở `GET /card`.

```json
{
  "agent_id": "team-08-news",
  "group_id": "team-08",
  "domain": "news",
  "capabilities": ["news_search", "news_summary", "sentiment", "trending_topics"],
  "endpoint": "https://news.team08.example",
  "status": "online",
  "version": "1.0.0"
}
```

| Trường | Kiểu | Ghi chú |
|---|---|---|
| `agent_id` | string | Duy nhất trong cả lớp. Đề xuất dạng `team-XX-<domain>` |
| `group_id` | string | `team-XX` |
| `domain` | string | `news`, `finance`, `weather`… |
| `capabilities` | string[] | Tên capability, dạng `snake_case` |
| `endpoint` | string | URL gốc **public**, không có dấu `/` ở cuối. Các path ở mục 2 nối vào sau URL này |
| `status` | `online` \| `degraded` \| `offline` | `degraded` = vẫn trả lời nhưng một phụ thuộc đang lỗi |
| `version` | string | semver |

## 2. Endpoint bắt buộc

| Method | Path | Trả về |
|---|---|---|
| GET | `/card` | Agent Card |
| GET | `/health` | `{"status": "online" \| "degraded" \| "offline", ...}`. HTTP 503 khi `offline` |
| POST | `/messages` | Nhận một Envelope `type=request`, trả **đồng bộ** một Envelope `response` hoặc `error` |

`/health` của News Agent có thêm `uptime_s`, `deps` (db, redis, crawler, llm, registry, broker, identity, tracer),
`modes` và `faults`. Các agent khác chỉ cần có `status`.

## 3. Message Envelope

```json
{
  "message_id": "m-6f1c…",
  "conversation_id": "conv-91ab…",
  "trace_id": "tr-0d3e…",
  "from": "team-05-finance",
  "to": "team-08-news",
  "type": "request",
  "capability": "news_summary",
  "reply_to": null,
  "payload": {"query": "giá vàng"},
  "token": "<token do Team 3 cấp cho team-05-finance>",
  "timestamp": 1793495337.37
}
```

| Trường | Bắt buộc | Ghi chú |
|---|---|---|
| `message_id` | có | Duy nhất. **Retry phải giữ nguyên** để bên nhận dedup |
| `conversation_id` | có | Một câu hỏi của người dùng = một conversation, có thể gồm nhiều message |
| `trace_id` | có | Truyền nguyên cho mọi message phát sinh từ cùng một yêu cầu, để Team 4 dựng lại đường đi |
| `from`, `to` | có | `agent_id` của bên gửi và bên nhận |
| `type` | có | `request` \| `response` \| `final` \| `error` |
| `capability` | khi `request` | Reply giữ nguyên giá trị này |
| `reply_to` | khi reply | Bằng `message_id` của request. Bên gửi bỏ qua reply có `reply_to` không khớp |
| `payload` | có | Object JSON, có thể rỗng `{}` |
| `token` | khi `request` | Token của bên gửi, xem mục 4 |
| `timestamp` | có | Unix time, đơn vị **giây** (số thực) |

Quy tắc reply:

- Giữ nguyên `conversation_id`, `trace_id`, `capability`. Đặt `reply_to = message_id` của request và đảo `from`/`to`.
- Reply có `message_id` mới và không cần `token`, vì reply đi về trên chính kết nối HTTP của request.
- Lỗi: `type=error`, `payload = {"error": "<mã>", "message": "<mô tả cho người đọc>"}`.

## 4. Xác thực

- Mỗi request mang `token` do Identity (Team 3) cấp cho **bên gửi**.
- Bên nhận validate token với Team 3 và kiểm tra danh tính trong token **trùng** với `from`. Token hợp lệ của agent
  khác vẫn bị từ chối, để không ai mạo danh được agent khác.
- Thiếu token, token sai, token hết hạn hoặc `from` giả: HTTP 401, `error=unauthorized`.
- Khi Team 3 không trả lời được: token đã validate thành công trong 60 giây qua vẫn được nhận, token lạ bị từ chối.

**Trước khi có Team 3**, để các nhóm thử với nhau, token local có dạng
`base64url({"sub": "<agent_id>", "exp": <unix giây>}) + "." + base64url(HMAC-SHA256(secret, phần trước dấu chấm))`,
ký bằng secret dùng chung (`LOCAL_SHARED_SECRET`). Code tham khảo: `backend/app/adapters/local/identity.py`.

## 5. Lỗi, retry và idempotency

| `error` | HTTP | Khi nào | Bên gửi có nên retry? |
|---|---|---|---|
| `bad_request` | 400 | Envelope sai, capability không hỗ trợ, payload không hợp lệ (message nói rõ trường nào) | Không |
| `unauthorized` | 401 | Xem mục 4 | Không (lấy token mới rồi gửi lại thì được) |
| `internal` | 500 | Lỗi bên trong khi xử lý | Có |
| `unavailable` | 503 | Đang quá tải, hoặc message cùng `message_id` đang được xử lý | Có, sau một khoảng chờ |
| `timeout` | 504 | Capability chạy quá giới hạn (News Agent: 8 giây) | Có |

**Idempotency.** Bên nhận dedup theo `message_id`:

- Message trùng với một message đã xử lý xong: trả lại **đúng reply cũ**, không xử lý lại.
- Message trùng với một message đang xử lý: chờ tối đa bằng thời gian timeout, hết thời gian thì trả `unavailable`.
- Lỗi tạm thời (`timeout`, `internal`) không được lưu làm reply, nên retry sau đó sẽ được xử lý lại từ đầu.

**Khi News Agent gọi agent khác**, để các nhóm biết agent của mình cần nhanh tới đâu:

- Mỗi lần gửi chờ tối đa **2,5 giây**. Hết thời gian, HTTP 5xx hoặc body không đọc được: retry **1 lần** sau
  0,5 giây, giữ nguyên `message_id` (token được cấp mới). Reply `type=error` thì không retry.
- Một câu hỏi của người dùng có hạn chót tổng **12 giây**.
- Với `stock_price`, News Agent hỏi **2 agent song song** để so giá. Agent nào lỗi thì chuyển sang agent kế tiếp
  có cùng capability. Không còn agent nào thì câu trả lời ghi rõ là thiếu phần đó.

## 6. Capability News Agent cung cấp

Tin lấy từ RSS của VnExpress, Tuổi Trẻ, Thanh Niên, Dân trí, VietnamNet và CafeF (mã nguồn: `vnexpress`, `tuoitre`,
`thanhnien`, `dantri`, `vietnamnet`, `cafef`), cập nhật mỗi 10 phút. Mã bài có dạng `a_<số>`. Thời gian theo
ISO 8601 có múi giờ.

Khi chưa có Gemini (hoặc Gemini lỗi), mọi capability vẫn trả lời bằng phương án không dùng LLM. Trường
`generated_by` hoặc `method` cho biết kết quả được tạo bằng cách nào.

### 6.1 `news_search`

| Request | Kiểu | Mặc định | Giới hạn |
|---|---|---|---|
| `query` | string | bắt buộc | 1–500 ký tự |
| `limit` | int | 10 | 1–50 |
| `from_date`, `to_date` | `YYYY-MM-DD` | không lọc | |
| `sources` | string[] | mọi nguồn | mã nguồn ở trên |
| `mode` | `keyword` \| `semantic` \| `hybrid` | `hybrid` | `semantic`/`hybrid` cần Gemini, không có thì tự về `keyword` |

```json
{
  "results": [
    {"id": "a_123", "title": "…", "snippet": "…", "url": "https://…", "source": "cafef",
     "published_at": "2026-11-08T08:30:00+07:00", "score": 0.82, "sentiment": "negative"}
  ],
  "count": 37,
  "freshness": "2026-11-08T08:50:00+07:00",
  "mode_used": "keyword"
}
```

`freshness` là lần crawl thành công gần nhất. `mode_used` là chế độ thật sự đã dùng. `sentiment` có thể là `null`.

### 6.2 `news_summary`

| Request | Kiểu | Mặc định | Giới hạn |
|---|---|---|---|
| `query` | string | bắt buộc | 1–500 ký tự |
| `limit` | int | 8 | 1–20 bài dùng để tóm tắt |
| `max_words` | int | 150 | 20–500 |

```json
{
  "summary": "…",
  "key_points": ["…", "…"],
  "sources": [{"id": "a_1", "url": "https://…", "source": "tuoitre"}],
  "generated_by": "gemini-3.5-flash-lite",
  "confidence": 0.7
}
```

`generated_by` là tên model, `extractive` (ghép câu từ bài gốc, không có LLM) hoặc `none` (không có bài nào khớp, khi
đó `sources` rỗng).

### 6.3 `sentiment`

Gửi **đúng một** trong ba trường `query`, `text` (tối đa 5000 ký tự), `article_ids` (tối đa 200 mã).
`window` (mặc định `7d`, dạng `<số>h` hoặc `<số>d`) giới hạn thời gian khi hỏi theo `query`.

```json
{"label": "negative", "score": -0.21, "breakdown": {"positive": 3, "neutral": 10, "negative": 7},
 "window": "7d", "n_articles": 20}
```

`score` nằm trong [-1, 1]. Với `text`, kết quả có thêm `method` (`llm` hoặc `lexicon`), `window` là `null` và
`n_articles` là 0.

### 6.4 `trending_topics`

| Request | Kiểu | Mặc định | Giới hạn |
|---|---|---|---|
| `window` | string | `24h` | `<số>h` hoặc `<số>d` |
| `limit` | int | 10 | 1–50 |
| `category` | string | mọi chuyên mục | ví dụ `kinh-te`, `tong-hop` |

```json
{"topics": [{"topic": "giá vàng", "count": 34, "delta_pct": 120, "sample_article_ids": ["a_9", "a_12"]}]}
```

`delta_pct` là mức tăng số bài so với khoảng thời gian liền trước, dài bằng `window`. Chủ đề được xếp theo mức
đột biến so với tần suất thường ngày, nên cụm từ lúc nào cũng xuất hiện sẽ không lọt vào danh sách.

## 7. Capability News Agent cần từ nhóm khác

Agent nào đăng ký các capability dưới đây sẽ được News Agent tự tìm qua Registry và gọi khi câu hỏi cần.
Nếu nhóm bạn đặt tên hoặc schema khác, báo nhóm mình để thống nhất.

| Capability | Request | Response mong đợi | News Agent nhớ trong |
|---|---|---|---|
| `stock_price` | `{"symbol": "TCB"}` (mã 3 chữ in hoa) | `{"symbol", "price", "currency", "as_of", "confidence"}` | 15 phút |
| `weather` | `{"location": "Hà Nội"}` | `{"location", "temperature_c", "condition", "as_of", "confidence"}` | 30 phút |

- `as_of`: Unix giây. `confidence`: 0–1, không gửi thì News Agent coi là 0,5.
- Hai agent báo giá khác nhau thì News Agent giữ cả hai, đánh dấu xung đột, và câu trả lời nêu rõ ai báo giá nào.
- Có thể gửi thêm trường khác. News Agent lưu nguyên `payload`.

## 8. Cần từ các nhóm hạ tầng

News Agent gọi hạ tầng qua 4 interface (`backend/app/ports.py`). Hiện mỗi interface có một bản local để chạy một
mình. Khi các nhóm công bố API, nhóm mình viết adapter tương ứng.

| Nhóm | News Agent cần | Đang dùng tạm |
|---|---|---|
| Team 1, Registry | `register(card)`, `heartbeat(agent_id, status)` (mỗi 15 giây), `find_by_capability(name) → card[]`, `list_agents()` | File YAML |
| Team 2, Broker | `send(envelope, target, timeout) → reply envelope`, đồng bộ | Gọi HTTP thẳng `POST {endpoint}/messages` |
| Team 3, Identity | `issue_token(agent_id)`, `validate(token) → {agent_id, expires_at}` hoặc không hợp lệ | HMAC, mục 4 |
| Team 4, Observability | Nhận event `{trace_id, message_id, agent_id, event, attrs, ts}` | Chỉ ghi vào DB của mình |

Khi một dịch vụ hạ tầng lỗi, News Agent vẫn chạy:

- Registry lỗi: dùng danh sách agent đã biết từ trước.
- Identity lỗi: dùng cache 60 giây như mục 4.
- Observability lỗi: trace vẫn ghi local.
- Broker lỗi: gọi HTTP thẳng nếu biết endpoint.

## 9. Câu hỏi còn mở

1. **Registry**: định danh agent bằng `agent_id` tự đặt hay Team 1 cấp? Agent không gửi heartbeat bao lâu thì
   bị coi là `offline`?
2. **Broker**: Team 2 chuyển message đồng bộ (request → reply trên cùng lời gọi) hay bất đồng bộ (gửi reply thành
   một message riêng về `/messages` của bên gửi)? Contract trên đang giả định đồng bộ. Nếu bất đồng bộ, cần thống nhất
   cách bên gửi nhận reply.
3. **Identity**: token có hạn bao lâu, có scope theo capability không? Team 3 có endpoint validate, hay public key để
   các agent tự kiểm?
4. **Observability**: định dạng event có theo OpenTelemetry không? Các agent nên đẩy event theo lô hay từng event?
5. `type=final` dùng khi nào? News Agent hiện chưa gửi. `/messages` chỉ nhận `type=request`, còn reply `final`
   được coi như `response`.
6. Giới hạn tần suất: mỗi agent được gọi agent khác bao nhiêu request mỗi phút?

## Thử nhanh

Khi News Agent đang chạy (`docker compose up`), gửi một message như agent khác:

```bash
cd backend
uv run python -m scripts.send news_summary '{"query": "giá vàng"}'
uv run python -m scripts.send news_search '{"query": "lãi suất"}' --dup        # gửi 2 lần cùng message_id
uv run python -m scripts.send news_search '{"query": "x"}' --spoof             # mạo danh: 401
```

`uv run python -m scripts.demo` chạy lần lượt 7 kịch bản nghiệm thu (spec §12) và kiểm tra từng bước.
