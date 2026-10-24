"""OpenAI-compatible /v1/audio/speech engine (e.g. Kokoro-FastAPI, openedai-speech)."""
from __future__ import annotations

import io

import httpx
import numpy as np
import soundfile as sf

from .base import TTSEngine, describe_voice


class OpenAICompatibleEngine(TTSEngine):
    name = "openai"
    sample_rate = 24000

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self._client = httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0))

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def ready(self) -> tuple[bool, str]:
        try:
            r = self._client.get(f"{self.base_url}/models", headers=self._headers())
            return r.status_code < 500, f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    def voices(self) -> list[dict]:
        # Kokoro-FastAPI exposes /v1/audio/voices; fall back to a sensible default list.
        try:
            r = self._client.get(f"{self.base_url}/audio/voices", headers=self._headers())
            if r.status_code == 200:
                data = r.json()
                ids = data.get("voices", data) if isinstance(data, dict) else data
                return [describe_voice(v) if isinstance(v, str) else v for v in ids]
        except Exception:  # noqa: BLE001
            pass
        return [describe_voice(v) for v in ("af_heart", "af_bella", "am_michael", "bf_emma", "bm_george")]

    def synthesize(self, text: str, voice: str, speed: float = 1.0, lang: str = "en-us") -> np.ndarray:
        r = self._client.post(
            f"{self.base_url}/audio/speech", headers=self._headers(),
            json={"model": self.model, "input": text, "voice": voice, "speed": speed, "response_format": "wav"},
        )
        r.raise_for_status()
        data, sr = sf.read(io.BytesIO(r.content), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != self.sample_rate:
            # Simple linear resample to keep one rate per rendition.
            n = int(len(data) * self.sample_rate / sr)
            data = np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data).astype(np.float32)
        return data
