"""Chunking + the text prefixes gemini-embedding-2 uses instead of task_type."""

from __future__ import annotations

WORDS_PER_CHUNK = 350  # roughly 500 tokens of Vietnamese text (spec §8)
MAX_CHUNKS = 20


def document_text(title: str | None, chunk: str) -> str:
    return f"title: {title or 'none'} | text: {chunk}"


def query_text(query: str) -> str:
    return f"task: search result | query: {query}"


def chunk_text(text: str, words_per_chunk: int = WORDS_PER_CHUNK, max_chunks: int = MAX_CHUNKS) -> list[str]:
    """Group paragraphs into chunks of at most `words_per_chunk` words; split paragraphs that are too long."""
    chunks: list[str] = []
    current: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        while len(words) > words_per_chunk:
            if current:
                chunks.append(" ".join(current))
                current = []
            chunks.append(" ".join(words[:words_per_chunk]))
            words = words[words_per_chunk:]
        if len(current) + len(words) > words_per_chunk:
            chunks.append(" ".join(current))
            current = []
        current.extend(words)
    if current:
        chunks.append(" ".join(current))
    return chunks[:max_chunks]
