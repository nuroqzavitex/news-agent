from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.agent.capabilities._common import parse_window
from app.agent.capabilities.news_summary import extractive_summary
from app.agent.capabilities.sentiment import SentimentPayload
from app.services.embedding import chunk_text, document_text, query_text
from app.services.llm import LLMBadOutput
from app.services.search import SearchHit, rrf_merge
from app.services.sentiment import label_for, lexicon_sentiment, llm_sentiments
from app.services.trending import phrases, trending
from tests.fake_gemini import FakeGemini


def test_chunk_text_respects_size_and_paragraphs():
    short = "một hai ba\nbốn năm"
    assert chunk_text(short, words_per_chunk=10) == ["một hai ba bốn năm"]
    assert chunk_text("a b c\nd e f\ng", words_per_chunk=4) == ["a b c", "d e f g"]
    assert chunk_text("x " * 25, words_per_chunk=10) == ["x " * 9 + "x", "x " * 9 + "x", "x x x x x"]
    assert chunk_text("") == []
    assert len(chunk_text("w " * 1000, words_per_chunk=10, max_chunks=3)) == 3


def test_embedding_prefixes():
    assert document_text("Giá vàng", "nội dung") == "title: Giá vàng | text: nội dung"
    assert document_text(None, "nội dung") == "title: none | text: nội dung"
    assert query_text("giá vàng") == "task: search result | query: giá vàng"


def test_lexicon_sentiment():
    assert lexicon_sentiment("Kinh tế phục hồi, lợi nhuận doanh nghiệp tăng trưởng").label == "positive"
    assert lexicon_sentiment("Tai nạn khiến 3 người tử vong, thiệt hại nặng").label == "negative"
    assert lexicon_sentiment("Hội nghị diễn ra tại Hà Nội").label == "neutral"
    assert lexicon_sentiment("Hội nghị diễn ra tại Hà Nội").score == 0
    # Whole words only: "bão" must not match inside "bãohoà"-like longer tokens.
    assert lexicon_sentiment("baobãohoà").score == 0
    one_hit = lexicon_sentiment("Lịch thi đấu giải vô địch quốc gia")
    assert one_hit.label == "neutral" and one_hit.score > 0  # a single keyword is weak evidence
    assert label_for(0.0) == "neutral"


def test_rrf_rewards_items_in_both_rankings():
    merged = rrf_merge([1, 2, 3], [3, 4])
    assert merged[0][0] == 3
    assert {i for i, _ in merged} == {1, 2, 3, 4}


def test_parse_window():
    assert parse_window("24h") == timedelta(hours=24)
    assert parse_window("7d") == timedelta(days=7)
    with pytest.raises(ValueError):
        parse_window("week")


def test_sentiment_payload_needs_exactly_one_input():
    assert SentimentPayload(query="TCB").query == "TCB"
    for bad in ({}, {"query": "a", "text": "b"}):
        with pytest.raises(ValidationError):
            SentimentPayload(**bad)
    with pytest.raises(ValidationError):
        SentimentPayload(query="a", window="tomorrow")


async def test_llm_sentiments_keeps_only_valid_entries():
    answer = (
        '[{"id": 1, "label": "positive", "score": 5}, {"id": 2, "label": "great", "score": 0},'
        ' {"id": 99, "label": "neutral", "score": 0}, {"id": 3, "label": "neutral", "score": "x"}, "junk"]'
    )
    llm = FakeGemini().client()
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": answer}]}}]})
    ))
    result = await llm_sentiments(llm, [(1, "a"), (2, "b"), (3, "c")], timeout_s=1)
    assert set(result) == {1}
    assert result[1].score == 1.0  # clamped
    assert result[1].source == "llm"


async def test_llm_sentiments_rejects_non_list():
    llm = FakeGemini().client()
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}]})
    ))
    with pytest.raises(LLMBadOutput):
        await llm_sentiments(llm, [(1, "a")], timeout_s=1)


def test_phrases_cut_at_stopwords_punctuation_and_numbers():
    found = phrases("Giá vàng miếng SJC hôm nay: tăng 2 triệu")
    assert "giá vàng" in found and "giá vàng miếng sjc" in found
    assert not any("hôm" in p or "nay" in p for p in found)
    assert "sjc tăng" not in found  # split by the stop words and the colon
    assert not any("2" in p.split() for p in found)


def test_trending_counts_prefers_longer_phrases_and_reports_delta():
    current = [
        (5, "Giá vàng miếng tăng mạnh"),
        (4, "Giá vàng miếng lập đỉnh"),
        (3, "Giá vàng miếng giảm sâu"),
        (2, "Đội tuyển bóng đá thắng"),
        (1, "Đội tuyển bóng đá thua"),
    ]
    topics = trending(current, ["Giá vàng miếng hạ nhiệt"], min_count=2)
    by_name = {t.topic: t for t in topics}
    assert "giá vàng miếng" in by_name
    assert "giá vàng" not in by_name and "vàng miếng" not in by_name
    assert "đội tuyển bóng đá" in by_name
    top = topics[0]
    assert (top.topic, top.count, top.delta_pct, top.sample_ids) == ("giá vàng miếng", 3, 200, [5, 4, 3])


def test_trending_ignores_rare_phrases():
    assert trending([(1, "Giá vàng tăng")], []) == []


def test_trending_ranks_unusual_phrases_above_everyday_ones():
    current = [(i, "Hội nghị thường niên họp") for i in range(5)] + [(i, "Tên lửa trúng đích") for i in range(5, 8)]
    baseline = ["Hội nghị thường niên họp"] * 40 + ["Chuyện khác hẳn"] * 20
    topics = trending(current, [], baseline)
    assert topics[0].topic == "tên lửa trúng đích"  # 3 surprising mentions beat 5 routine ones
    # With too little history, raw counts decide.
    assert trending(current, [], baseline[:10])[0].topic == "hội nghị thường niên họp"


def test_stop_phrases_are_not_topics():
    assert "việt nam" not in phrases("Việt Nam đạt được") and "tin tức" not in phrases("Tin tức sáng")


def _hit(snippet: str) -> SearchHit:
    return SearchHit(id="a_1", title="Tiêu đề", snippet=snippet, url="u", source="s",
                     published_at=None, score=0, sentiment=None)


def test_extractive_summary_takes_first_sentences_within_budget():
    hits = [_hit("Câu một ngắn. Câu thừa."), _hit("Câu hai cũng ngắn! Thừa."), _hit("Câu ba rất rất rất dài.")]
    assert extractive_summary(hits, max_words=7) == "Câu một ngắn. Câu hai cũng ngắn!"
    # The first sentence alone is over budget: truncate rather than return nothing.
    assert extractive_summary([_hit("một hai ba bốn năm")], max_words=2) == "một hai"
