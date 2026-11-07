import json

import httpx
import pytest

from app.agent.coordinator import ConflictingValue, StepResult, compose_from_template
from app.agent.planner import extract_topic, find_location, find_ticker, make_plan, plan_by_rules
from app.services.memory import comparable
from tests.fake_gemini import FakeGemini

KNOWN = {"news_summary", "news_search", "sentiment", "trending_topics"}


def caps(question: str) -> list[str]:
    return [s.capability for s in plan_by_rules(question).steps]


def test_stock_question_gets_news_tone_and_price():
    plan = plan_by_rules("Tin tức ảnh hưởng thế nào tới cổ phiếu TCB tuần này?")
    assert plan.method == "rules"
    assert [(s.capability, s.payload) for s in plan.steps] == [
        ("news_summary", {"query": "TCB"}),
        ("sentiment", {"query": "TCB", "window": "7d"}),
        ("stock_price", {"symbol": "TCB"}),
    ]


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Giá vàng hôm nay thế nào?", ["news_summary"]),
        ("Thời tiết ở Đà Nẵng ngày mai ra sao?", ["weather"]),
        ("Hôm nay có tin gì nổi bật?", ["trending_topics"]),
        ("Lạm phát tác động thế nào tới người dân?", ["news_summary", "sentiment"]),
    ],
)
def test_rules_choose_capabilities(question, expected):
    assert caps(question) == expected


def test_find_ticker_needs_a_hint_and_skips_currencies():
    assert find_ticker("cổ phiếu TCB") == "TCB"
    assert find_ticker("Giá VCB hôm nay") == "VCB"
    assert find_ticker("Tổ chức WHO họp") is None  # no hint that this is about shares
    assert find_ticker("Giá USD hôm nay") is None
    assert find_ticker("Giá vàng ở TP.HCM") is None


def test_extract_topic_keeps_keywords_only():
    assert extract_topic("Giá vàng hôm nay thế nào?") == "Giá vàng"
    assert extract_topic("Bão số 7 đổ bộ ở đâu?") == "Bão số 7 đổ bộ"
    assert extract_topic("cổ phiếu TCB", symbol="TCB") == "TCB"
    assert extract_topic("thế nào?")  # nothing but question words: still something to search


def test_find_location():
    assert find_location("Thời tiết ở Đà Nẵng") == "Đà Nẵng"
    assert find_location("Thời tiết tại Quy Nhơn hôm nay") == "Quy Nhơn"
    assert find_location("thời tiết hôm nay") == "Hà Nội"


def _llm_answering(text: str):
    llm = FakeGemini().client()
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})
    ))
    return llm


async def test_llm_plan_keeps_only_known_capabilities():
    answer = json.dumps({"steps": [
        {"capability": "news_search", "payload": {"query": "vàng"}},
        {"capability": "delete_everything", "payload": {}},
        {"capability": "sentiment", "payload": "not a dict"},
        "junk",
    ]})
    plan = await make_plan("x", llm=_llm_answering(answer), known=KNOWN, timeout_s=1)
    assert plan.method == "llm" and [s.capability for s in plan.steps] == ["news_search"]


async def test_llm_plan_limits_steps():
    answer = json.dumps({"steps": [{"capability": "news_search", "payload": {"query": str(i)}} for i in range(20)]})
    assert len((await make_plan("x", llm=_llm_answering(answer), known=KNOWN, timeout_s=1)).steps) == 5


@pytest.mark.parametrize("text", ['{"steps": []}', '{"steps": "no"}', "[]", "not json"])
async def test_useless_llm_plan_falls_back_to_rules(text):
    plan = await make_plan("Giá vàng hôm nay?", llm=_llm_answering(text), known=KNOWN, timeout_s=1)
    assert plan.method == "rules" and plan.steps


async def test_no_key_uses_rules():
    plan = await make_plan("Giá vàng?", llm=FakeGemini().client(api_key=""), known=KNOWN, timeout_s=1)
    assert plan.method == "rules"


def test_template_mentions_every_outcome():
    text = compose_from_template([
        StepResult(capability="news_summary", request={}, status="ok", data={"summary": "Tóm tắt tin."}),
        StepResult(
            capability="stock_price", request={}, status="stale",
            data={"symbol": "TCB", "price": 31500, "currency": "VND", "source_agent": "finance-a"},
            conflicts=[ConflictingValue(agent="finance-b", data={"symbol": "TCB", "price": 32000, "currency": "VND"})],
        ),
        StepResult(capability="weather", request={}, status="missing"),
    ])
    assert "Tóm tắt tin." in text
    assert "31.500 VND" in text and "theo finance-a" in text and "đã cũ" in text
    assert "xung đột" in text and "finance-b" in text and "32.000" in text
    assert "Không lấy được weather" in text


def test_template_for_empty_results():
    assert compose_from_template([])
    empty = StepResult(capability="news_summary", request={}, status="ok", data={"summary": ""})
    assert "Không tìm thấy" in compose_from_template([empty])


def test_comparable_ignores_fields_that_always_differ():
    assert comparable({"price": 1, "as_of": 5, "confidence": 0.9}) == comparable({"price": 1, "as_of": 6})
    assert comparable({"price": 1}) != comparable({"price": 2})
