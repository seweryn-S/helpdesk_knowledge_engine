from app.utils.text_content_filter import TextContentFilter, TextContentCategory


def test_empty_and_whitespace_are_non_semantic():
    flt = TextContentFilter()
    assert flt.classify("") == TextContentCategory.NON_SEMANTIC
    assert flt.classify("   ") == TextContentCategory.NON_SEMANTIC


def test_simple_polite_phrases_are_non_semantic():
    flt = TextContentFilter()
    for text in ["Tak", "ok", "Dziękuję", "dziekuje", "Pozdrawiam serdecznie"]:
        assert flt.classify(text) == TextContentCategory.NON_SEMANTIC


def test_short_ack_sequences_are_non_semantic():
    flt = TextContentFilter()
    assert flt.classify("tak ok") == TextContentCategory.NON_SEMANTIC
    assert flt.classify("dziekuje dzieki") == TextContentCategory.NON_SEMANTIC


def test_longer_text_with_content_is_semantic():
    flt = TextContentFilter()
    assert flt.classify("Tak, ale mam jeszcze jedno pytanie") == TextContentCategory.SEMANTIC
    assert flt.classify("Dziękuję za pomoc z logowaniem") == TextContentCategory.SEMANTIC

