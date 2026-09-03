"""Which language the robot is speaking, and when that is allowed to change.

The language is explicit session state, not something re-derived per turn: a
Chinese speaker who says one English word mid-sentence should not flip the
robot, and a low-confidence guess from the transcriber should never move it
at all. This phase ships English and Chinese; Malay is deferred, so a Malay
tag is treated like any other unsupported tag -- recognised as "not one of
ours" and ignored, never crashed on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ALLOWED_LANGUAGES = ('en', 'zh')
DEFAULT_LANGUAGE = 'en'


def normalise_language(raw: Optional[str]) -> Optional[str]:
    """Reduce a BCP-47-ish tag to a supported base language, or None.

    Whisper reports 'zh', browsers and vendor configs report 'zh-CN', and the
    LLM will occasionally answer 'en_US'. All three mean the same thing here.
    """
    if not raw:
        return None
    base = str(raw).strip().lower().replace('_', '-').split('-')[0]
    return base if base in ALLOWED_LANGUAGES else None


@dataclass(frozen=True)
class LanguagePolicy:
    """Decides whether a detected language replaces the session language."""

    default: str = DEFAULT_LANGUAGE
    switch_confidence: float = 0.7

    def next_language(self, current: str, detected: Optional[str],
                      confidence: float) -> str:
        current = normalise_language(current) or self.default
        candidate = normalise_language(detected)
        if candidate is None or candidate == current:
            return current
        if confidence < self.switch_confidence:
            return current
        return candidate
