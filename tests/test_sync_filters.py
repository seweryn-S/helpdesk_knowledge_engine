import pytest

from app.services.sync import SyncService


def _service_with_patterns(patterns: list[str]) -> SyncService:
    service = SyncService.__new__(SyncService)
    service._thread_filter_patterns = SyncService._compile_thread_filters(patterns)
    return service


def test_should_skip_details_matches_regex() -> None:
    service = _service_with_patterns([r"\."])
    assert service._should_skip_details("trailing dot.") is True
    assert service._should_skip_details("no dot here") is False


def test_should_skip_details_handles_crlf_patterns() -> None:
    service = _service_with_patterns([r"^\r?\n$", r"^\.\r?\n$", r"^\r?\n\r?\n$"])
    assert service._should_skip_details("\r\n") is True
    assert service._should_skip_details(".\r\n") is True
    assert service._should_skip_details("\r\n\r\n") is True
    assert service._should_skip_details("valid text") is False


def test_compile_thread_filters_validates_regex() -> None:
    with pytest.raises(ValueError):
        SyncService._compile_thread_filters(["["])
