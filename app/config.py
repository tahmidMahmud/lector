"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    v = os.environ.get(name)
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("LECTOR_DATA_DIR", "/data")).resolve()
        self.audio_dir = Path(os.environ.get("LECTOR_AUDIO_DIR", str(self.data_dir / "audio"))).resolve()
        self.cache_dir = self.data_dir / "cache"
        self.db_path = Path(os.environ.get("LECTOR_DB_PATH", str(self.data_dir / "lector.db"))).resolve()

        # Kavita connection. Either an API key (Kavita: User settings -> 3rd party clients)
        # or a username + password. The API key is preferred.
        self.kavita_url = os.environ.get("KAVITA_URL", "http://kavita:5000").rstrip("/")
        self.kavita_api_key = os.environ.get("KAVITA_API_KEY", "")
        self.kavita_username = os.environ.get("KAVITA_USERNAME", "")
        self.kavita_password = os.environ.get("KAVITA_PASSWORD", "")
        # Which Kavita library types to import. 2 = Book (Kavita LibraryType enum).
        self.kavita_library_types = [
            int(x) for x in os.environ.get("KAVITA_LIBRARY_TYPES", "2").split(",") if x.strip()
        ]
        # Map Kavita's container paths onto this container's mounts, e.g. "/books=/books;/other=/mnt/other".
        self.kavita_path_map = self._parse_path_map(os.environ.get("KAVITA_PATH_MAP", "/books=/books"))
        self.sync_interval_minutes = _int("SYNC_INTERVAL_MINUTES", 60)

        # TTS
        self.tts_engine = os.environ.get("TTS_ENGINE", "kokoro").lower()
        self.kokoro_model = os.environ.get("KOKORO_MODEL", "/models/kokoro-v1.0.onnx")
        self.kokoro_voices = os.environ.get("KOKORO_VOICES", "/models/voices-v1.0.bin")
        self.kokoro_auto_download = _bool("KOKORO_AUTO_DOWNLOAD", True)
        self.tts_openai_url = os.environ.get("TTS_OPENAI_URL", "http://localhost:8880/v1").rstrip("/")
        self.tts_openai_key = os.environ.get("TTS_OPENAI_KEY", "not-needed")
        self.tts_openai_model = os.environ.get("TTS_OPENAI_MODEL", "kokoro")
        self.default_voice = os.environ.get("DEFAULT_VOICE", "af_heart")
        self.default_speed = float(os.environ.get("DEFAULT_SPEED", "1.0"))
        self.tts_threads = _int("TTS_THREADS", 0)  # 0 = onnxruntime default
        self.audio_format = os.environ.get("AUDIO_FORMAT", "mp3").lower()  # mp3 | m4a
        self.audio_bitrate = os.environ.get("AUDIO_BITRATE", "64k")

        # Auth / multi-user
        self.secret_key = os.environ.get("LECTOR_SECRET_KEY", "")
        self.allow_registration = _bool("ALLOW_REGISTRATION", False)
        self.allow_user_requests = _bool("ALLOW_USER_REQUESTS", True)
        self.session_days = _int("SESSION_DAYS", 30)
        self.cookie_secure = _bool("COOKIE_SECURE", False)

        self.log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    @staticmethod
    def _parse_path_map(raw: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for pair in raw.split(";"):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            src, dst = pair.split("=", 1)
            out.append((src.rstrip("/") or "/", dst.rstrip("/") or "/"))
        # Longest prefix first
        out.sort(key=lambda p: -len(p[0]))
        return out

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.audio_dir, self.cache_dir, self.db_path.parent):
            d.mkdir(parents=True, exist_ok=True)

    def load_or_create_secret(self) -> str:
        if self.secret_key:
            return self.secret_key
        f = self.data_dir / "secret.key"
        if f.exists():
            self.secret_key = f.read_text().strip()
        else:
            self.secret_key = secrets.token_urlsafe(48)
            f.write_text(self.secret_key)
            try:
                f.chmod(0o600)
            except OSError:
                pass
        return self.secret_key

    def map_kavita_path(self, p: str) -> str:
        for src, dst in self.kavita_path_map:
            if p == src or p.startswith(src + "/"):
                return dst + p[len(src):]
        return p


settings = Settings()
