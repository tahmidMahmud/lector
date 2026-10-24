from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class TTSEngine(ABC):
    name: str = "base"
    sample_rate: int = 24000

    @abstractmethod
    def voices(self) -> list[dict]:
        """[{id, name, language, gender}]"""

    @abstractmethod
    def synthesize(self, text: str, voice: str, speed: float = 1.0, lang: str = "en-us") -> np.ndarray:
        """Return float32 mono samples at ``self.sample_rate``."""

    def ready(self) -> tuple[bool, str]:
        return True, "ok"


VOICE_META = {
    "a": ("American English", "en-us"), "b": ("British English", "en-gb"), "e": ("Spanish", "es"),
    "f": ("French", "fr-fr"), "h": ("Hindi", "hi"), "i": ("Italian", "it"), "j": ("Japanese", "ja"),
    "p": ("Brazilian Portuguese", "pt-br"), "z": ("Mandarin Chinese", "cmn"),
}


def describe_voice(vid: str) -> dict:
    """Kokoro voice ids look like ``af_heart``: language letter, gender letter, name."""
    lang_code, gender = (vid[0], vid[1]) if len(vid) > 2 and vid[2] == "_" else ("a", "f")
    lang_name, espeak = VOICE_META.get(lang_code, ("Unknown", "en-us"))
    name = vid.split("_", 1)[1] if "_" in vid else vid
    return {"id": vid, "name": name.capitalize(), "language": lang_name, "lang": espeak,
            "gender": "female" if gender == "f" else "male"}
