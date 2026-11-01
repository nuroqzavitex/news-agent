# ADR 001 — Dùng Redis Streams cho pipeline nội bộ

- **Trạng thái:** Đã chấp nhận (01/11/2026, spec 0.2)
- **Thay thế:** Redpanda (Kafka API) trong spec 0.1

## Bối cảnh

Pipeline crawl → xử lý cần một hàng đợi có ack, giao lại khi consumer chết, và DLQ. Spec 0.1 chọn Redpanda.
Nhưng nhóm chỉ có khoảng 250 giờ công cho phần code, và Team 2 của lớp phụ trách Broker, nên dùng Kafka nội bộ
có rủi ro bị xem là làm trùng phần việc của họ.

## Quyết định

Dùng **Redis Streams + consumer group** (Redis đã có sẵn cho dedup, cache và lock):

- `crawl.jobs` → crawler, `news.raw` → processor, `news.dlq` cho message lỗi 3 lần.
- Chỉ `XACK` sau khi ghi DB thành công (at-least-once). Upsert theo `url` nên việc giao lại vẫn idempotent.
- `XAUTOCLAIM` sau 60 giây sẽ nhận lại message của consumer đã chết. Thời gian chờ này cũng là backoff khi retry.
- Redis chạy với `--appendonly yes` để giữ stream khi restart.
- Stream chỉ chứa metadata (không chứa HTML), `MAXLEN ~ 10000`.

Message **giữa các agent** không đi qua đây mà qua Broker của Team 2, hoặc HTTP trực tiếp khi chạy local.

## Hệ quả

- Bớt một container và thư viện `aiokafka`. Toàn bộ helper nằm gọn trong `app/workers/streams.py` (khoảng 150 dòng, có unit test).
- Mất các tính năng partition/replay theo offset của Kafka. MVP không cần các tính năng này.
- Redis trở thành điểm lỗi chung. Nếu Redis down, pipeline dừng nhưng agent vẫn trả lời bằng dữ liệu đã có trong Postgres (`status=degraded`).
