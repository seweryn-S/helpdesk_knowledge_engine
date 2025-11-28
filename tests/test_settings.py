from app.core.config import Settings


def _base_settings(**overrides):
    data = {
        "helpdesk_api_base_url": "https://example.com/api",
        "helpdesk_api_key": "key",
        "helpdesk_ticket_url_prefix": "https://example.com/ticket?",
        "embedding_api_base_url": "https://embeddings.example.com/v1",
        "embedding_model_name": "model",
    }
    data.update(overrides)
    return data


def test_thread_filter_patterns_empty_string_parses_as_empty_list() -> None:
    settings = Settings.model_validate(_base_settings(thread_filter_patterns=""))
    assert settings.thread_filter_patterns == []


def test_thread_filter_patterns_csv_string() -> None:
    settings = Settings.model_validate(_base_settings(thread_filter_patterns="foo,bar"))
    assert settings.thread_filter_patterns == ["foo", "bar"]
