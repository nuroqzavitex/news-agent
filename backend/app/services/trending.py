"""Trending topics: phrases that appear in unusually many articles right now, compared with their usual rate.

Works entirely without an LLM. Vietnamese words are often several space-separated syllables ("giá vàng"), so
candidates are n-grams of 2 to 5 syllables, cut at punctuation and stop words. Counting alone would surface
generic phrases ("việt nam"), so phrases are ranked by how far they exceed a longer baseline.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.services.textproc import normalize_text

MIN_COUNT = 3
MAX_N = 5
SAMPLE_SIZE = 3
SUBPHRASE_RATIO = 0.7
MIN_BASELINE_DOCS = 50  # with less history than this, rates mean nothing and raw counts decide

STOPWORDS = frozenset(
    """
    à ai anh bà bạn bao bằng bị bởi các cả cái cần cho chị chỉ chiếc chính chưa chuyện có con còn của cùng đã đang đây
    để đến điều đó được gì giữa hay hơn khi không là lại làm lên lúc mà mình mới một mỗi muốn năm nào này nên nếu ngày
    người nhà nhất nhiều như những nói nơi ở ông phải qua quá ra rằng rất rồi sao sau sẽ so sự tại thì theo thế thêm
    tháng tôi trên trong trước từ tuần và vào vẫn vì việc với vừa xuống về
    hôm nay mai bao nhiêu thế nào tiền
    """.split()
)
STOP_PHRASES = frozenset(
    {
        "tin tức", "đầu tiên", "lần đầu", "bất ngờ", "trở thành", "sẵn sàng", "việt nam", "cho biết", "hiện nay",
        "thông tin",
        "dân trí",  # its summaries all start with "(Dân trí) -"
    }
)
_SEGMENT_SPLIT = re.compile(r"[^\w\s]+")


@dataclass(frozen=True)
class Topic:
    topic: str
    count: int
    delta_pct: int
    sample_ids: list[int]


def phrases(text: str) -> set[str]:
    """Candidate phrases of one article's text."""
    found: set[str] = set()
    for segment in _SEGMENT_SPLIT.split(normalize_text(text).lower()):
        run: list[str] = []
        for word in [*segment.split(), ""]:  # the empty word flushes the last run
            if word and word not in STOPWORDS and not word.isdigit():
                run.append(word)
                continue
            for n in range(2, MAX_N + 1):
                found.update(" ".join(run[i : i + n]) for i in range(len(run) - n + 1))
            run = []
    return found - STOP_PHRASES


def _document_counts(texts: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for text in texts:
        for phrase in phrases(text):
            counts[phrase] += 1
    return counts


def _same_story(a: str, a_ids: list[int], b: str, b_ids: list[int]) -> bool:
    """Two phrases sharing words and mostly the same articles are one topic."""
    if not set(a.split()) & set(b.split()):
        return False
    shared = len(set(a_ids) & set(b_ids))
    return shared >= SUBPHRASE_RATIO * min(len(a_ids), len(b_ids))


def trending(
    current: Sequence[tuple[int, str]],
    previous: Iterable[str],
    baseline: Sequence[str] = (),
    *,
    limit: int = 10,
    min_count: int = MIN_COUNT,
) -> list[Topic]:
    """`current`: (article_id, text) pairs, newest first. `previous`: texts of the preceding window of equal length,
    used for delta_pct. `baseline`: texts of a longer earlier period, used to tell news from everyday phrases.
    """
    members: dict[str, list[int]] = defaultdict(list)
    for article_id, text in current:
        for phrase in phrases(text):
            members[phrase].append(article_id)
    candidates = {p: ids for p, ids in members.items() if len(ids) >= min_count}

    # Drop a phrase that is nearly always part of a longer one: keep "giá vàng miếng", not "vàng miếng" too.
    kept = {
        phrase: ids
        for phrase, ids in candidates.items()
        if not any(
            other != phrase and f" {phrase} " in f" {other} " and len(other_ids) >= SUBPHRASE_RATIO * len(ids)
            for other, other_ids in candidates.items()
        )
    }

    usual = _document_counts(baseline)

    def surprise(phrase: str, ids: list[int]) -> float:
        if len(baseline) < MIN_BASELINE_DOCS:
            return float(len(ids))
        rate_now = len(ids) / len(current)
        rate_usual = (usual.get(phrase, 0) + 1) / (len(baseline) + 1)
        return len(ids) * math.log(rate_now / rate_usual)

    scored = [(surprise(p, ids), p, ids) for p, ids in kept.items()]
    # Ties go to the longer phrase, which usually names the topic more completely.
    scored.sort(key=lambda s: (-s[0], -len(s[1]), s[1]))

    # Sliding n-grams overlap ("giá vàng miếng", "vàng miếng diễn"): report one per group of articles.
    selected: list[tuple[str, list[int]]] = []
    for score, phrase, ids in scored:
        if score <= 0 or len(selected) == limit:
            break
        if not any(_same_story(phrase, ids, other, other_ids) for other, other_ids in selected):
            selected.append((phrase, ids))

    before = _document_counts(previous)
    return [
        Topic(
            topic=phrase,
            count=len(ids),
            delta_pct=round((len(ids) - before.get(phrase, 0)) / max(before.get(phrase, 0), 1) * 100),
            sample_ids=ids[:SAMPLE_SIZE],
        )
        for phrase, ids in selected
    ]
