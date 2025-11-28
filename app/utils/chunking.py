from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, List


@dataclass
class TextChunk:
    text: str
    start: int
    end: int
    sentence_start: int
    sentence_end: int


_SENTENCE_REGEX = re.compile(r"[^.!?]+(?:[.!?]+|\Z)", re.S)


def _split_sentences(text: str) -> List[tuple[int, int]]:
    sentences: List[tuple[int, int]] = []
    for match in _SENTENCE_REGEX.finditer(text):
        start, end = match.span()
        if not text[start:end].strip():
            continue
        sentences.append((start, end))
    if not sentences and text:
        sentences.append((0, len(text)))
    return sentences


def _chunk_single_sentence(body: str, max_length: int) -> List[TextChunk]:
    """Fallback splitter for very long texts that don't contain clear sentences."""
    if max_length <= 0 or len(body) <= max_length:
        return [TextChunk(text=body, start=0, end=len(body), sentence_start=0, sentence_end=0)]

    chunks: List[TextChunk] = []
    start = 0
    length = len(body)

    while start < length:
        end = min(start + max_length, length)
        if end < length:
            # Try to split on whitespace/newline to avoid chopping words mid-way.
            whitespace = body.rfind(" ", start + 1, end)
            newline = body.rfind("\n", start + 1, end)
            split_at = max(whitespace, newline)
            if split_at > start:
                end = split_at
        chunks.append(
            TextChunk(text=body[start:end], start=start, end=end, sentence_start=0, sentence_end=0)
        )
        start = end

    return chunks


def _chunk_sentence_span(body: str, sent_start: int, sent_end: int, sentence_idx: int, max_length: int) -> List[TextChunk]:
    """Chunk a single sentence span while preserving absolute offsets."""
    relative_chunks = _chunk_single_sentence(body[sent_start:sent_end], max_length)
    chunks: List[TextChunk] = []
    for rel in relative_chunks:
        chunks.append(
            TextChunk(
                text=rel.text,
                start=sent_start + rel.start,
                end=sent_start + rel.end,
                sentence_start=sentence_idx,
                sentence_end=sentence_idx,
            )
        )
    return chunks


def chunk_text(text: str, max_length: int) -> List[TextChunk]:
    """Split text into sentence-aware chunks with positional metadata."""
    body = text or ""
    sentences = _split_sentences(body)
    sentence_count = len(sentences)
    if not body:
        return [TextChunk(text="", start=0, end=0, sentence_start=0, sentence_end=0)]

    if max_length <= 0 or len(body) <= max_length:
        last_sentence_index = max(sentence_count - 1, 0)
        return [TextChunk(text=body, start=0, end=len(body), sentence_start=0, sentence_end=last_sentence_index)]

    if sentence_count <= 1:
        return _chunk_single_sentence(body, max_length)

    chunks: List[TextChunk] = []
    chunk_start: int | None = None
    chunk_end: int | None = None
    chunk_sentence_start: int | None = None
    chunk_sentence_end: int | None = None

    for sentence_idx, (sent_start, sent_end) in enumerate(sentences):
        sentence_length = sent_end - sent_start

        if sentence_length > max_length:
            if chunk_start is not None:
                chunks.append(
                    TextChunk(
                        text=body[chunk_start:chunk_end],
                        start=chunk_start,
                        end=chunk_end if chunk_end is not None else chunk_start,
                        sentence_start=chunk_sentence_start if chunk_sentence_start is not None else sentence_idx,
                        sentence_end=chunk_sentence_end if chunk_sentence_end is not None else sentence_idx,
                    )
                )
                chunk_start = None
                chunk_end = None
                chunk_sentence_start = None
                chunk_sentence_end = None
            chunks.extend(_chunk_sentence_span(body, sent_start, sent_end, sentence_idx, max_length))
            continue

        if chunk_start is None:
            chunk_start = sent_start
            chunk_end = sent_end
            chunk_sentence_start = sentence_idx
            chunk_sentence_end = sentence_idx
            continue

        assert chunk_sentence_start is not None
        assert chunk_end is not None

        prospective_length = sent_end - chunk_start
        if max_length > 0 and prospective_length > max_length:
            chunks.append(
                TextChunk(
                    text=body[chunk_start:chunk_end],
                    start=chunk_start,
                    end=chunk_end,
                    sentence_start=chunk_sentence_start,
                    sentence_end=chunk_sentence_end if chunk_sentence_end is not None else sentence_idx - 1,
                )
            )
            chunk_start = sent_start
            chunk_end = sent_end
            chunk_sentence_start = sentence_idx
            chunk_sentence_end = sentence_idx
            continue

        chunk_end = sent_end
        chunk_sentence_end = sentence_idx

    if chunk_start is not None:
        if chunk_end is None:
            chunk_end = len(body)
        if chunk_sentence_start is None:
            chunk_sentence_start = 0
        if chunk_sentence_end is None:
            chunk_sentence_end = sentence_count - 1

        chunks.append(
            TextChunk(
                text=body[chunk_start:chunk_end],
                start=chunk_start,
                end=chunk_end,
                sentence_start=chunk_sentence_start,
                sentence_end=chunk_sentence_end,
            )
        )

    return chunks


def merge_texts(texts: Iterable[str]) -> str:
    return "\n\n".join([t for t in texts if t])
