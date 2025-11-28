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


def test_compile_thread_filters_validates_regex() -> None:
    with pytest.raises(ValueError):
        SyncService._compile_thread_filters(["["])
