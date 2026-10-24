"""TTS engine factory."""
from __future__ import annotations

from ..config import settings
from .base import TTSEngine

_engine: TTSEngine | None = None


def get_engine() -> TTSEngine:
    global _engine
    if _engine is None:
        if settings.tts_engine == "openai":
            from .openai_engine import OpenAICompatibleEngine
            _engine = OpenAICompatibleEngine(settings.tts_openai_url, settings.tts_openai_key,
                                             settings.tts_openai_model)
        else:
            from .kokoro_engine import KokoroEngine
            _engine = KokoroEngine(settings.kokoro_model, settings.kokoro_voices,
                                   auto_download=settings.kokoro_auto_download,
                                   threads=settings.tts_threads)
    return _engine
