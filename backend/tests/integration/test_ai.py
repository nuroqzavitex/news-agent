"""Enricher, vector search and the AI capabilities on real Postgres + Redis, with a fake Gemini."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.adapters.local.identity import LocalIdentity
from app.contracts import Envelope
from app.db.models import Article, ArticleChunk
from app.main import create_app
from app.services.embedding import query_text
from app.services.llm import LLMError
from app.services.search import SearchService
from app.settings import Settings
from app.workers.enricher import enrich_batch
from app.workers.processor import process_item
from tests.fake_gemini import FakeGemini, vector_for
from tests.integration.conftest import TEST_DATABASE_URL, TEST_REDIS_URL

pytestmark = pytest.mark.integration

SECRET = "integration-secret"
ME = "team-08-news"
ARTICLES = [
    ("Giá vàng tăng mạnh", "Giá vàng SJC tăng trưởng, lợi nhuận phục hồi", "Giá vàng miếng SJC tăng.\nĐoạn hai vàng."),
    ("Kim loại quý lập đỉnh", "Nhà đầu tư mua vàng tích trữ", None),
    ("Tai nạn giao thông", "Vụ tai nạn khiến 2 người tử vong", "Tai nạn nghiêm trọng trên quốc lộ."),
]


@pytest.fixture
async def articles(sessions):
    async with sessions() as s:
        await s.execute(insert(Article).values([
            {"source_id": 1, "url": f"https://news.test/{i}", "url_hash": str(i), "title": title,
             "summary": summary, "content": content, "category": "tong-hop"}
            for i, (title, summary, content) in enumerate(ARTICLES)
        ]))
        await s.commit()
    return sessions


async def _all(sessions) -> list[Article]:
    async with sessions() as s:
        return list((await s.scalars(select(Article).order_by(Article.id))).all())


async def test_without_key_only_lexicon_sentiment_is_filled(articles):
    fake = FakeGemini()
    llm = fake.client(api_key="")
    assert await enrich_batch(articles, llm) == 3
    rows = await _all(articles)
    assert [a.sentiment_label for a in rows] == ["positive", "neutral", "negative"]
    assert {a.sentiment_source for a in rows} == {"lexicon"}
    assert {a.enriched_at for a in rows} == {None}  # still pending for when a key is added
    assert fake.requests == []
    assert await enrich_batch(articles, llm) == 0  # nothing left to do, no busy loop


async def test_adding_a_key_backfills_old_articles(articles):
    await enrich_batch(articles, FakeGemini().client(api_key=""))
    assert await enrich_batch(articles, FakeGemini().client()) == 3
    rows = await _all(articles)
    assert {a.sentiment_source for a in rows} == {"llm"}
    assert all(a.enriched_at is not None for a in rows)
    assert rows[0].sentiment_label == "positive" and rows[2].sentiment_label == "negative"
    async with articles() as s:
        chunks = (await s.scalars(select(ArticleChunk).order_by(ArticleChunk.article_id))).all()
    # Every article has a chunk; the one without full text uses its title and summary.
    assert {c.article_id for c in chunks} == {a.id for a in rows}
    assert await enrich_batch(articles, FakeGemini().client()) == 0


async def test_gemini_outage_keeps_articles_pending_without_burning_attempts(articles):
    fake = FakeGemini()
    fake.fail_with = 429
    with pytest.raises(LLMError):
        await enrich_batch(articles, fake.client())
    rows = await _all(articles)
    assert {a.enriched_at for a in rows} == {None}
    assert {a.enrich_attempts for a in rows} == {0}
    assert {a.sentiment_source for a in rows} == {"lexicon"}  # still usable during the outage
    fake.fail_with = None
    assert await enrich_batch(articles, fake.client()) == 3


async def test_rejected_batch_gives_up_after_three_attempts(articles):
    fake = FakeGemini()
    fake.fail_with = 400
    llm = fake.client()
    for _ in range(3):
        assert await enrich_batch(articles, llm) == 3
    assert await enrich_batch(articles, llm) == 0
    assert {a.enrich_attempts for a in await _all(articles)} == {3}


async def test_sentiment_outage_after_embedding_is_retried_without_duplicate_chunks(articles):
    fake = FakeGemini()
    fake.fail_generate_with = lambda: httpx.Response(503, text="overloaded")
    with pytest.raises(LLMError):
        await enrich_batch(articles, fake.client())
    async with articles() as s:
        first = await s.scalar(select(func.count()).select_from(ArticleChunk))
    assert first > 0
    fake.fail_generate_with = None
    assert await enrich_batch(articles, fake.client()) == 3
    async with articles() as s:
        assert await s.scalar(select(func.count()).select_from(ArticleChunk)) == first


async def test_unusable_sentiment_output_keeps_lexicon_and_finishes(articles):
    fake = FakeGemini()
    fake.fail_generate_with = lambda: httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
    )
    assert await enrich_batch(articles, fake.client()) == 3
    rows = await _all(articles)
    assert {a.sentiment_source for a in rows} == {"lexicon"}
    assert all(a.enriched_at is not None for a in rows)


async def test_changed_content_sends_article_back_to_enricher(sessions, redis):
    from app.services.politeness import Politeness

    url = "https://news.test/changing.html"
    body = "Nội dung đầy đủ của bài viết về giá vàng hôm nay được cập nhật liên tục trong ngày. " * 3
    pages = {url: httpx.Response(200, text=f"<html><body><article><p>{body}</p></article></body></html>")}

    def handler(request: httpx.Request) -> httpx.Response:
        return pages.get(str(request.url), httpx.Response(404))

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    polite = Politeness(redis, http, "test", min_interval_ms=1)
    item = {"url": url, "url_hash": "c", "title": "Bài", "summary": "tóm", "source_id": 1, "source_code": "testnews"}
    article_id = await process_item(item, sessions=sessions, http=http, polite=polite)
    await enrich_batch(sessions, FakeGemini().client())

    # Redelivery with the same text keeps the enrichment.
    await process_item(item, sessions=sessions, http=http, polite=polite)
    async with sessions() as s:
        assert (await s.get(Article, article_id)).enriched_at is not None

    # New text makes it pending again.
    pages[url] = httpx.Response(410)
    await process_item(item, sessions=sessions, http=http, polite=polite)
    async with sessions() as s:
        assert (await s.get(Article, article_id)).enriched_at is None


async def test_semantic_and_hybrid_search(articles, redis):
    fake = FakeGemini()
    llm = fake.client(redis=redis)
    await enrich_batch(articles, llm)
    search = SearchService(articles, llm, redis)

    # "vàng" is not in "Tai nạn giao thông"; the query vector matches the two gold articles.
    semantic = await search.search(query="thị trường vàng", mode="semantic", limit=2)
    assert semantic.mode_used == "semantic"
    assert {r.title for r in semantic.results} == {"Giá vàng tăng mạnh", "Kim loại quý lập đỉnh"}
    assert semantic.results[0].score == pytest.approx(1.0)

    hybrid = await search.search(query="giá vàng", mode="hybrid")
    assert hybrid.mode_used == "hybrid"
    assert hybrid.results[0].title == "Giá vàng tăng mạnh"  # found by both rankings
    assert hybrid.results[0].score == 1.0

    # An unrelated article is not a match just because it is the nearest thing available.
    assert "Tai nạn giao thông" not in {r.title for r in hybrid.results}

    # The fake model calls this one negative; the filter applies to the semantic ranking too.
    only_negative = await search.search(query="giá vàng", mode="hybrid", sentiment="negative")
    assert [r.title for r in only_negative.results] == ["Kim loại quý lập đỉnh"]

    assert vector_for(query_text("giá vàng"))[0] == 1.0
    embed_calls = len(fake.requests)
    await search.search(query="giá vàng", mode="hybrid")
    assert len(fake.requests) == embed_calls  # the query vector came from the cache


async def test_search_degrades_to_keyword(articles, redis):
    assert (await SearchService(articles).search(query="giá vàng", mode="hybrid")).mode_used == "keyword"

    no_key = SearchService(articles, FakeGemini().client(api_key=""), redis)
    result = await no_key.search(query="gia vang", mode="semantic")
    assert result.mode_used == "keyword" and result.results[0].title == "Giá vàng tăng mạnh"

    # Key present, but nothing has been embedded yet.
    not_embedded = await SearchService(articles, FakeGemini().client(), redis).search(query="giá vàng", mode="hybrid")
    assert not_embedded.mode_used == "keyword"

    fake = FakeGemini()
    fake.fail_with = 500
    outage = await SearchService(articles, fake.client(), redis).search(query="giá vàng", mode="hybrid")
    assert outage.mode_used == "keyword" and outage.count == 1


@pytest.fixture
async def agent(articles, redis):
    """agent-api over ASGI, with lexicon sentiment filled in and no API key."""
    await enrich_batch(articles, FakeGemini().client(api_key=""))
    client, state = await _agent_client("")
    yield client, state
    await _close(client, state)


async def _agent_client(key: str):
    settings = Settings(_env_file=None, database_url=TEST_DATABASE_URL, redis_url=TEST_REDIS_URL, agent_id=ME,
                        local_shared_secret=SECRET, gemini_api_key=key)
    app = create_app(settings)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test")
    return client, app.state.agent


async def _close(client, state) -> None:
    await client.aclose()
    await state.http.aclose()
    await state.redis.aclose()
    await state.engine.dispose()


async def _send(client: httpx.AsyncClient, capability: str, payload: dict) -> dict:
    token = await LocalIdentity(SECRET).issue_token("tester")
    env = Envelope(from_="tester", to=ME, capability=capability, payload=payload, token=token)
    resp = await client.post("/messages", json=env.wire())
    return resp.json()


async def test_card_lists_all_capabilities(agent):
    client, _ = agent
    card = (await client.get("/card")).json()
    assert card["capabilities"] == ["news_search", "news_summary", "sentiment", "trending_topics"]


async def test_summary_without_key_is_extractive(agent):
    client, _ = agent
    body = await _send(client, "news_summary", {"query": "giá vàng", "max_words": 30})
    assert body["type"] == "response"
    payload = body["payload"]
    assert payload["generated_by"] == "extractive"
    assert payload["confidence"] == 0.3
    assert "Giá vàng SJC" in payload["summary"]
    assert payload["sources"][0]["url"] == "https://news.test/0"
    events = [e["event"] for e in (await client.get(f"/api/v1/traces/{body['trace_id']}")).json()]
    assert "llm.fallback" in events and "llm.call" not in events


async def test_summary_for_unknown_topic_is_empty_not_an_error(agent):
    client, _ = agent
    payload = (await _send(client, "news_summary", {"query": "xyzzyquux"}))["payload"]
    assert payload == {"summary": "", "key_points": [], "sources": [], "generated_by": "none", "confidence": 0.0}


async def test_summary_with_llm_is_cached(articles, redis):
    fake = FakeGemini()
    client, state = await _agent_client("test-key")
    state.handler._deps["llm"]._http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    try:
        first = await _send(client, "news_summary", {"query": "giá vàng"})
        assert first["payload"]["summary"] == "Tóm tắt từ model."
        assert first["payload"]["key_points"] == ["Ý một", "Ý hai"]
        assert first["payload"]["generated_by"] == "gemini-3.5-flash-lite"
        calls = len(fake.requests)
        second = await _send(client, "news_summary", {"query": "giá vàng"})
        assert second["payload"]["summary"] == "Tóm tắt từ model."
        assert len(fake.requests) == calls
    finally:
        await _close(client, state)


async def test_summary_survives_llm_outage_and_health_reports_it(articles, redis):
    fake = FakeGemini()
    fake.fail_with = 500
    client, state = await _agent_client("test-key")
    state.handler._deps["llm"]._http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    try:
        body = await _send(client, "news_summary", {"query": "giá vàng"})
        assert body["type"] == "response"
        assert body["payload"]["generated_by"] == "extractive"
        health = (await client.get("/health")).json()
        assert health["deps"]["llm"] == "error (HTTP 500)"
        assert health["status"] == "degraded"
    finally:
        await _close(client, state)


async def test_sentiment_for_query_text_and_articles(agent):
    client, _ = agent
    by_query = (await _send(client, "sentiment", {"query": "tai nạn"}))["payload"]
    assert by_query["label"] == "negative"
    assert by_query["n_articles"] == 1
    assert by_query["breakdown"] == {"positive": 0, "neutral": 0, "negative": 1}
    assert by_query["window"] == "7d"

    by_text = (await _send(client, "sentiment", {"text": "Doanh nghiệp có lãi, kinh tế phục hồi"}))["payload"]
    assert by_text["label"] == "positive" and by_text["method"] == "lexicon"

    by_ids = (await _send(client, "sentiment", {"article_ids": ["a_1", "a_2", "a_3"]}))["payload"]
    assert by_ids["n_articles"] == 3 and by_ids["window"] is None

    nothing = (await _send(client, "sentiment", {"query": "xyzzyquux"}))["payload"]
    assert nothing["label"] == "neutral" and nothing["n_articles"] == 0

    bad = await _send(client, "sentiment", {})
    assert bad["type"] == "error" and bad["payload"]["error"] == "bad_request"


async def test_trending_topics(sessions, redis):
    async with sessions() as s:
        await s.execute(insert(Article).values([
            {"source_id": 1, "url": f"https://news.test/t{i}", "url_hash": f"t{i}",
             "title": f"Giá vàng miếng diễn biến số {i}", "category": "kinh-te"}
            for i in range(4)
        ]))
        await s.commit()
    client, state = await _agent_client("")
    try:
        payload = (await _send(client, "trending_topics", {"window": "24h"}))["payload"]
        assert payload["n_articles"] == 4
        assert len(payload["topics"]) == 1  # overlapping phrases of one story are reported once
        top = payload["topics"][0]
        assert top["count"] == 4 and "vàng miếng" in top["topic"]
        assert len(top["sample_article_ids"]) == 3
        other = (await _send(client, "trending_topics", {"category": "the-thao"}))["payload"]
        assert other["topics"] == []
    finally:
        await _close(client, state)
