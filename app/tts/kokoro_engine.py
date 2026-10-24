"""Built-in Kokoro-82M engine via kokoro-onnx (CPU, no PyTorch)."""
from __future__ import annotations

import logging
import os
import threading
import urllib.request
from pathlib import Path

import numpy as np

from .base import TTSEngine, describe_voice

log = logging.getLogger("lector.tts")

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"


class KokoroEngine(TTSEngine):
    name = "kokoro"
    sample_rate = 24000

    def __init__(self, model_path: str, voices_path: str, auto_download: bool = True, threads: int = 0) -> None:
        self.model_path = Path(model_path)
        self.voices_path = Path(voices_path)
        self.auto_download = auto_download
        self.threads = threads
        self._kokoro = None
        self._lock = threading.Lock()
        self._error: str | None = None

    # -- model lifecycle ----------------------------------------------------
    def _download(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        log.info("Downloading %s -> %s", url, dest)
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:  # noqa: S310
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        tmp.rename(dest)

    def _load(self):
        if self._kokoro is not None:
            return self._kokoro
        with self._lock:
            if self._kokoro is not None:
                return self._kokoro
            if not self.model_path.exists() or not self.voices_path.exists():
                if not self.auto_download:
                    raise RuntimeError(
                        f"Kokoro model files missing ({self.model_path}, {self.voices_path}) and "
                        "KOKORO_AUTO_DOWNLOAD is off")
                if not self.model_path.exists():
                    self._download(MODEL_URL, self.model_path)
                if not self.voices_path.exists():
                    self._download(VOICES_URL, self.voices_path)
            if self.threads:
                os.environ.setdefault("ONNX_PROVIDER", "CPUExecutionProvider")
                import onnxruntime as ort
                so = ort.SessionOptions()
                so.intra_op_num_threads = self.threads
                from kokoro_onnx import Kokoro
                sess = ort.InferenceSession(str(self.model_path), so, providers=["CPUExecutionProvider"])
                self._kokoro = Kokoro.from_session(sess, str(self.voices_path))
            else:
                from kokoro_onnx import Kokoro
                self._kokoro = Kokoro(str(self.model_path), str(self.voices_path))
            log.info("Kokoro model loaded from %s", self.model_path)
            return self._kokoro

    def ready(self) -> tuple[bool, str]:
        try:
            self._load()
            return True, "ok"
        except Exception as e:  # noqa: BLE001
            self._error = str(e)
            return False, str(e)

    # -- API ---------------------------------------------------------------
    def voices(self) -> list[dict]:
        try:
            ids = sorted(self._load().get_voices())
        except Exception:  # noqa: BLE001
            ids = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "am_adam", "am_michael",
                   "bf_emma", "bf_isabella", "bm_george", "bm_lewis"]
        return [describe_voice(v) for v in ids]

    def synthesize(self, text: str, voice: str, speed: float = 1.0, lang: str = "en-us") -> np.ndarray:
        k = self._load()
        samples, sr = k.create(text, voice=voice, speed=speed, lang=lang)
        if sr != self.sample_rate:
            # kokoro is fixed at 24 kHz; guard anyway
            self.sample_rate = sr
        return np.asarray(samples, dtype=np.float32)
