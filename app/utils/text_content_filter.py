# Copyright (C) 2026 Seweryn Sitarski <seweryn.sitarski@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Sequence


class TextContentCategory(str, Enum):
    SEMANTIC = "semantic"
    NON_SEMANTIC = "non_semantic"


@dataclass
class TextContentFilterConfig:
    simple_non_semantic_phrases: Sequence[str]
    ack_tokens: Sequence[str]
    max_ack_tokens: int
    min_chars_for_semantic: int = 0
    min_tokens_for_semantic: int = 0


@dataclass
class ClassificationResult:
    category: TextContentCategory
    reason: str


class TextContentFilter:
    def __init__(self, config: TextContentFilterConfig | None = None):
        if config is None:
            config = self._default_config()
        self.config = config
        self._simple_phrases = {self._normalize_simple(p) for p in config.simple_non_semantic_phrases}
        self._ack_tokens = {self._normalize_simple(t) for t in config.ack_tokens}

    @staticmethod
    def _default_config() -> TextContentFilterConfig:
        simple_non_semantic_phrases: List[str] = [
            "tak",
            "nie",
            "ok",
            "oki",
            "okej",
            "spoko",
            "dziękuję",
            "dziekuje",
            "dzięki",
            "dzieki",
            "dziękuję bardzo",
            "dziekuje bardzo",
            "dzięki bardzo",
            "dzieki bardzo",
            "pozdrawiam",
            "pozdrawiam serdecznie",
            "dzień dobry",
            "dzien dobry",
            "dobry wieczór",
            "dobry wieczor",
            "dobranoc",
            "do widzenia",
        ]
        ack_tokens: List[str] = [
            "tak",
            "ok",
            "oki",
            "okej",
            "spoko",
            "dziękuję",
            "dziekuje",
            "dzięki",
            "dzieki",
            "pozdrawiam",
            "dzień",
            "dobry",
        ]
        return TextContentFilterConfig(
            simple_non_semantic_phrases=simple_non_semantic_phrases,
            ack_tokens=ack_tokens,
            max_ack_tokens=3,
            min_chars_for_semantic=8,
            min_tokens_for_semantic=2,
        )

    @staticmethod
    def _normalize_simple(text: str) -> str:
        return text.strip().casefold()

    @staticmethod
    def normalize(text: str) -> str:
        """Normalize text for non-semantic check."""
        body = (text or "").strip()
        if not body:
            return ""
        body = body.casefold()
        body = re.sub(r"\s+", " ", body)
        body = body.strip(" .,!?:;-_\"'()[]{}„”…")
        return body

    def explain(self, text: str) -> ClassificationResult:
        if not text:
            return ClassificationResult(TextContentCategory.NON_SEMANTIC, "empty_text")

        normalized = self.normalize(text)
        if not normalized:
            return ClassificationResult(TextContentCategory.NON_SEMANTIC, "empty_after_normalization")

        tokens = normalized.split()

        if (
            self.config.min_chars_for_semantic > 0
            and len(normalized) < self.config.min_chars_for_semantic
        ) or (
            self.config.min_tokens_for_semantic > 0
            and len(tokens) < self.config.min_tokens_for_semantic
        ):
            return ClassificationResult(TextContentCategory.NON_SEMANTIC, "too_short")

        if self._is_simple_phrase(normalized):
            return ClassificationResult(TextContentCategory.NON_SEMANTIC, "simple_phrase")

        if self._is_short_ack_sequence(tokens):
            return ClassificationResult(TextContentCategory.NON_SEMANTIC, "ack_sequence")

        return ClassificationResult(TextContentCategory.SEMANTIC, "semantic")

    def classify(self, text: str) -> TextContentCategory:
        return self.explain(text).category

    def is_semantic(self, text: str) -> bool:
        return self.classify(text) == TextContentCategory.SEMANTIC

    def _is_simple_phrase(self, normalized: str) -> bool:
        return normalized in self._simple_phrases

    def _is_short_ack_sequence(self, tokens: List[str]) -> bool:
        if not tokens:
            return True
        if len(tokens) > self.config.max_ack_tokens:
            return False
        return all(token in self._ack_tokens for token in tokens)
