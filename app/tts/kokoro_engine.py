"""Built-in Kokoro-82M engine via kokoro-onnx (CPU, no PyTorch)."""
from __future__ import annotations

import logging
import threading
import urllib.request
from pathlib import Path

import numpy as np

from .base import TTSEngine, describe_voice

log = logging.getLogger("lector.tts")

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# espeak-ng language codes accepted by kokoro-onnx's phonemizer for the Kokoro v1.0 voices.
SUPPORTED_LANGS = {"en-us", "en-gb", "es", "fr-fr", "hi", "it", "ja", "pt-br", "cmn"}
MIN_SPEED, MAX_SPEED = 0.5, 2.0  # kokoro-onnx asserts this range

# phonemizer logs "words count mismatch" for nearly every chunk; it's harmless noise.
logging.getLogger("phonemizer").setLevel(logging.ERROR)
logging.getLogger("phonemizer").propagate = False  # it re-inits its level on backend creation


class KokoroEngine(TTSEngine):
    name = "kokoro"
    sample_rate = 24000

    def __init__(self, model_path: str, voices_path: str, auto_download: bool = True, threads: int = 0) -> None:
        self.model_path = Path(model_path)
        self.voices_path = Path(voices_path)
        self.auto_download = auto_download
        self.threads = max(0, int(threads or 0))
        self._kokoro = None
        self._voice_ids: set[str] = set()
        self._lock = threading.Lock()        # model loading
        self._synth_lock = threading.Lock()  # espeak/phonemizer isn't guaranteed thread-safe
        self._error: str | None = None
        self._warm = False

    # -- model lifecycle ----------------------------------------------------
    def _download(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        log.info("Downloading %s -> %s", url, dest)
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:  # noqa: S310
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)

    def _make(self):
        from kokoro_onnx import Kokoro
        if self.threads:
            try:
                import onnxruntime as ort
                so = ort.SessionOptions()
                so.intra_op_num_threads = self.threads
                so.inter_op_num_threads = 1
                sess = ort.InferenceSession(str(self.model_path), so, providers=["CPUExecutionProvider"])
                k = Kokoro.from_session(sess, str(self.voices_path))
                log.info("Kokoro using %d onnxruntime threads", self.threads)
                return k
            except Exception as e:  # noqa: BLE001
                log.warning("TTS_THREADS=%d could not be applied (%s); using onnxruntime defaults",
                            self.threads, e)
        return Kokoro(str(self.model_path), str(self.voices_path))

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
            k = self._make()
            try:
                self._voice_ids = set(k.get_voices())
            except Exception:  # noqa: BLE001
                self._voice_ids = set()
            self._kokoro = k
            self._error = None
            log.info("Kokoro model loaded from %s (%d voices)", self.model_path, len(self._voice_ids))
            return self._kokoro

    def ready(self) -> tuple[bool, str]:
        try:
            self._load()
            return True, "ok"
        except Exception as e:  # noqa: BLE001
            self._error = str(e)
            return False, str(e)

    def warmup(self) -> None:
        if self._warm:
            return
        self.synthesize("Hello.", "af_heart" if "af_heart" in self._voices() else self._first_voice())
        self._warm = True

    # -- API ---------------------------------------------------------------
    def _voices(self) -> set[str]:
        self._load()
        return self._voice_ids

    def _first_voice(self) -> str:
        v = sorted(self._voices())
        if not v:
            raise RuntimeError("Kokoro voices file contains no voices")
        return v[0]

    def has_voice(self, voice: str) -> bool | None:
        try:
            ids = self._voices()
        except Exception:  # noqa: BLE001
            return None
        return (voice in ids) if ids else None

    def voices(self) -> list[dict]:
        try:
            ids = sorted(self._voices())
        except Exception:  # noqa: BLE001
            ids = []
        if not ids:
            ids = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "am_adam", "am_michael",
                   "bf_emma", "bf_isabella", "bm_george", "bm_lewis"]
        return [describe_voice(v) for v in ids]

    def synthesize(self, text: str, voice: str, speed: float = 1.0, lang: str = "en-us") -> np.ndarray:
        k = self._load()
        if self._voice_ids and voice not in self._voice_ids:
            raise ValueError(f"unknown Kokoro voice {voice!r}")
        if lang not in SUPPORTED_LANGS:
            lang = describe_voice(voice)["lang"]
        speed = min(MAX_SPEED, max(MIN_SPEED, float(speed or 1.0)))
        with self._synth_lock:
            samples, sr = k.create(text, voice=voice, speed=speed, lang=lang)
        if sr != self.sample_rate:
            # kokoro is fixed at 24 kHz; guard anyway
            self.sample_rate = sr
        return np.asarray(samples, dtype=np.float32).reshape(-1)
