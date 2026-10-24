# syntax=docker/dockerfile:1
# Lector - self-hosted audiobook server for a Kavita library (Kokoro TTS on CPU + ffmpeg).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LECTOR_DATA_DIR=/data \
    KOKORO_MODEL=/models/kokoro-v1.0.onnx \
    KOKORO_VOICES=/models/voices-v1.0.bin \
    HOME=/home/lector

# ffmpeg: chapter encoding + M4B export. libsndfile1: soundfile. gosu: drop root -> PUID/PGID.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libsndfile1 gosu ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 1000 lector \
 && useradd -u 1000 -g lector -d /home/lector -m -s /usr/sbin/nologin lector \
 && mkdir -p /data /models /books \
 && chown lector:lector /data /models

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

VOLUME ["/data", "/models"]
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=4)" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# Exactly one worker: the narration job queue runs as a thread inside the app process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
