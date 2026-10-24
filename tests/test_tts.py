"""TTS engine tests. The Kokoro tests use the real model when its files are available."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # no pytest config in the repo yet
os.environ.setdefault("LECTOR_DATA_DIR", tempfile.mkdtemp(prefix="lector-test-"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from app.tts.base import VOICE_META, TTSEngine, describe_voice  # noqa: E402
from app.tts.kokoro_engine import SUPPORTED_LANGS, KokoroEngine  # noqa: E402

_DEFAULT_MODELS = Path.home() / ".claude/skills/metaphor-art-reel/.kokoro-models"
MODEL = Path(os.environ.get("KOKORO_MODEL") or _DEFAULT_MODELS / "kokoro-v1.0.onnx")
VOICES = Path(os.environ.get("KOKORO_VOICES") or _DEFAULT_MODELS / "voices-v1.0.bin")
needs_model = pytest.mark.skipif(not (MODEL.exists() and VOICES.exists()),
                                 reason="Kokoro model files not available (set KOKORO_MODEL/KOKORO_VOICES)")


# ---------------------------------------------------------------------------
# describe_voice / language codes
# ---------------------------------------------------------------------------
def test_describe_voice_basic():
    d = describe_voice("af_heart")
    assert d == {"id": "af_heart", "name": "Heart", "language": "American English", "lang": "en-us",
                 "gender": "female"}
    assert describe_voice("bm_george")["lang"] == "en-gb"
    assert describe_voice("bm_george")["gender"] == "male"


def test_describe_voice_odd_ids():
    assert describe_voice("xx_thing")["lang"] == "en-us"  # unknown language letter
    assert describe_voice("heart")["lang"] == "en-us"
    assert describe_voice("heart")["name"] == "Heart"


def test_voice_langs_are_valid_kokoro_espeak_codes():
    for letter, (_name, lang) in VOICE_META.items():
        assert lang in SUPPORTED_LANGS, (letter, lang)
        assert describe_voice(f"{letter}f_test")["lang"] == lang


def test_voice_langs_supported_by_espeak():
    pytest.importorskip("phonemizer")
    espeakng_loader = pytest.importorskip("espeakng_loader")
    from phonemizer.backend import EspeakBackend
    from phonemizer.backend.espeak.wrapper import EspeakWrapper
    EspeakWrapper.set_library(espeakng_loader.get_library_path())
    EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
    langs = EspeakBackend.supported_languages()
    assert SUPPORTED_LANGS <= set(langs)


def test_base_engine_defaults():
    class E(TTSEngine):
        def voices(self):
            return []

        def synthesize(self, text, voice, speed=1.0, lang="en-us"):
            return np.zeros(1, dtype=np.float32)

    e = E()
    assert e.ready() == (True, "ok")
    assert e.warmup() is None
    assert e.has_voice("af_heart") is None


# ---------------------------------------------------------------------------
# KokoroEngine without model files
# ---------------------------------------------------------------------------
def test_kokoro_missing_files_not_ready(tmp_path):
    e = KokoroEngine(str(tmp_path / "nope.onnx"), str(tmp_path / "nope.bin"), auto_download=False)
    ok, msg = e.ready()
    assert not ok and "KOKORO_AUTO_DOWNLOAD" in msg
    assert e.has_voice("af_heart") is None
    assert any(v["id"] == "af_heart" for v in e.voices())  # fallback list
    with pytest.raises(RuntimeError):
        e.synthesize("hi", "af_heart")


# ---------------------------------------------------------------------------
# KokoroEngine with the real model
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def kokoro():
    e = KokoroEngine(str(MODEL), str(VOICES), auto_download=False)
    ok, msg = e.ready()
    assert ok, msg
    return e


@needs_model
def test_kokoro_synthesize(kokoro):
    kokoro.warmup()
    a = kokoro.synthesize("The quick brown fox jumps over the lazy dog.", "af_heart", 1.0, "en-us")
    assert a.dtype == np.float32 and a.ndim == 1
    assert kokoro.sample_rate == 24000
    dur = len(a) / kokoro.sample_rate
    assert 1.5 < dur < 6
    rms = float(np.sqrt(np.mean(a ** 2)))
    assert rms > 0.01 and float(np.max(np.abs(a))) <= 1.0


@needs_model
def test_kokoro_voices_and_validation(kokoro):
    ids = {v["id"] for v in kokoro.voices()}
    assert "af_heart" in ids and len(ids) > 20
    assert kokoro.has_voice("af_heart") is True
    assert kokoro.has_voice("zz_nobody") is False
    with pytest.raises(ValueError, match="unknown Kokoro voice"):
        kokoro.synthesize("Hello.", "zz_nobody")


@needs_model
def test_kokoro_clamps_speed_and_fixes_lang(kokoro):
    fast = kokoro.synthesize("One two three four five six seven eight.", "af_heart", 5.0, "en-us")
    normal = kokoro.synthesize("One two three four five six seven eight.", "af_heart", 1.0, "en-us")
    assert 0 < len(fast) < len(normal)
    # An invalid espeak code falls back to the voice's language instead of erroring.
    a = kokoro.synthesize("Good morning.", "bf_emma", 1.0, "english")
    assert a.size > 0


@needs_model
def test_kokoro_threads_option_uses_session():
    e = KokoroEngine(str(MODEL), str(VOICES), auto_download=False, threads=2)
    ok, msg = e.ready()
    assert ok, msg
    opts = e._kokoro.sess.get_session_options()
    assert opts.intra_op_num_threads == 2
    assert e.synthesize("Hi there.", "am_michael").size > 0
