from app.utils.chunking import TextChunk, chunk_text


def test_chunk_text_keeps_sentences_together():
    text = "Zdanie pierwsze. Zdanie drugie jest dłuższe! Ostatnie?"
    chunks = chunk_text(text, max_length=30)

    assert len(chunks) == 3
    assert all(isinstance(chunk, TextChunk) for chunk in chunks)
    # Każdy fragment powinien kończyć się pełnym zdaniem
    assert chunks[0].text.strip().endswith(".")
    assert chunks[1].text.strip().endswith("!")
    assert chunks[2].text.strip().endswith("?")


def test_chunk_text_returns_metadata():
    text = "Pierwsze zdanie. Drugie zdanie."
    chunks = chunk_text(text, max_length=50)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.start == 0
    assert chunk.end == len(text)
    assert chunk.sentence_start == 0
    assert chunk.sentence_end == 1


def test_chunk_text_splits_single_long_sentence():
    text = "x" * 120
    chunks = chunk_text(text, max_length=50)

    assert len(chunks) == 3
    assert all(len(chunk.text) <= 50 for chunk in chunks)
    assert chunks[0].start == 0
    assert chunks[-1].end == len(text)
    assert all(chunk.sentence_start == 0 and chunk.sentence_end == 0 for chunk in chunks)
