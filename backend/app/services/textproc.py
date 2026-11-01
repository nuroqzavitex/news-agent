"""Xử lý văn bản: làm sạch HTML, chuẩn hóa unicode, simhash 64-bit để phát hiện bài gần trùng."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gidzl"}

MASK64 = (1 << 64) - 1


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return normalize_text(html.unescape(_TAG_RE.sub(" ", text)))


def canonical_url(url: str) -> str:
    """Bỏ fragment và tham số tracking để cùng một bài không bị lưu nhiều lần."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in _TRACKING_PARAMS]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()


def _h64(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")


def simhash(text: str, shingle: int = 3) -> int:
    """Simhash 64-bit trên shingle 3 từ; trả về số có dấu để vừa cột BIGINT của Postgres."""
    words = _WORD_RE.findall(normalize_text(text).lower())
    if not words:
        return 0
    grams = [" ".join(words[i : i + shingle]) for i in range(max(1, len(words) - shingle + 1))]
    weights = [0] * 64
    for g in grams:
        h = _h64(g)
        for bit in range(64):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    value = sum(1 << bit for bit in range(64) if weights[bit] > 0)
    return value - (1 << 64) if value >= (1 << 63) else value


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & MASK64).bit_count()
