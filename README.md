# Lector

Lector is a self-hosted audiobook server for an existing [Kavita](https://www.kavitareader.com/)
ebook library. It reads your EPUBs from Kavita, narrates them with the local
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) text-to-speech model (CPU only, via
`kokoro-onnx`), encodes chapterized audio with ffmpeg, and serves it in a mobile-friendly web
player. Nothing leaves your machine.

Stack: Python 3.12, FastAPI, SQLite, kokoro-onnx / onnxruntime, ffmpeg.

## Features

- **Multi-user.** The first account becomes the admin; admins create other users (or turn on
  open registration). Progress, bookmarks and player preferences are per user.
- **Kavita sync.** Book libraries are imported automatically (hourly by default) or on demand
  with **Sync now**; covers, authors and summaries come from Kavita.
- **Kokoro voices.** All Kokoro v1.0 voices (American/British English and more), with a short
  preview for each, and a selectable narration speed. Several narrations (voice + speed) of the
  same book can coexist.
- **Clean text.** Project Gutenberg boilerplate is trimmed automatically: only the text between
  the `*** START OF ... ***` and `*** END OF ... ***` markers is narrated.
- **Chapterized audio.** Chapters follow the EPUB table of contents (falling back to headings or
  spine files) and are encoded one file per chapter, so you can listen while the rest is still
  being generated.
- **Player.** Resume across devices, bookmarks with notes, sleep timer, playback speed, skip
  back/forward, and **read-along** (the current paragraph is highlighted from per-paragraph
  timings).
- **M4B export.** Build a single chaptered `.m4b` for any finished narration and download it for
  other audiobook apps.
- **Queue control.** Admins can pause, resume, cancel, retry or delete narrations; a status page
  shows the current chapter, speed (realtime factor), Kavita connectivity and disk usage.
- **Alternative TTS backend.** Instead of the built-in engine, point Lector at any
  OpenAI-compatible `/v1/audio/speech` server, e.g.
  [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) (GPU) or openedai-speech
  (`TTS_ENGINE=openai`).

## How it works

```
  +-----------+   REST API (API key)    +-----------------------------------------------+
  |  Kavita   | ----------------------> |                    Lector                     |
  | (library) |   series, volumes,      |                                               |
  +-----------+   covers, file paths    |  sync ---> SQLite (books, users, progress)    |
        |                               |   |                                           |
        | /books (same host folder,     |   v                                           |
        | mounted read-only in both)    |  EPUB text pipeline                           |
        +-----------------------------> |   - flatten XHTML, strip Gutenberg header/    |
                                        |     license, split by TOC into chapters,      |
                                        |     chunk into sentences                      |
                                        |   |                                           |
                                        |   v                                           |
                                        |  Kokoro TTS (onnxruntime, CPU, 24 kHz)        |
                                        |   |                                           |
                                        |   v                                           |
                                        |  ffmpeg -> one mp3 per chapter (+ timings)    |
                                        |   |            \-> optional chaptered .m4b    |
                                        |   v                                           |
                                        |  FastAPI: /api + web player (resume,          |
                                        |  bookmarks, sleep timer, speed, read-along)   |
                                        +-----------------------------------------------+
                                                             |
                                                  browser / phone (PWA)
```

Narration jobs run one at a time in a background thread inside the single app process (which is
why uvicorn must run with exactly one worker).

## Quick start (Docker)

Prerequisites: Docker (Docker Desktop on macOS is fine; images build natively for arm64) and a
running Kavita with at least one **Book** library of EPUBs.

1. **Configure.**
   ```sh
   cp .env.example .env
   ```
   Edit `.env`:
   - `KAVITA_API_KEY` - in Kavita open the user menu (top right) -> **Settings** ->
     **3rd Party Clients** (the same key is shown with the OPDS URL) and copy the **API key**.
     Lector exchanges it for a session token through `/api/Plugin/authenticate`. Alternatively
     leave it empty and set `KAVITA_USERNAME` / `KAVITA_PASSWORD`.
   - `LECTOR_DATA_PATH` - where the database and generated audio go (use a big disk).
   - `BOOKS_PATH` - the **host** folder that Kavita mounts as `/books`.
   - `KOKORO_MODELS_PATH` - a folder containing `kokoro-v1.0.onnx` and `voices-v1.0.bin`, or an
     empty writable folder (they are downloaded on first use, ~350 MB).
2. **Create the data folder** (Docker would otherwise create it as root):
   ```sh
   mkdir -p /path/to/lector-data   # whatever you set as LECTOR_DATA_PATH
   ```
3. **Start.**
   ```sh
   docker compose up -d --build
   docker compose logs -f lector
   ```
4. Open **http://&lt;host&gt;:5055** and create the admin account (the first account created is
   the admin; the setup screen disappears afterwards).

The compose file joins the external Docker network `media-server_default` (created by the
media-server compose project that runs Kavita), so Lector reaches Kavita at
`http://kavita:5000`. **If Kavita is not on that network**, delete the two `networks:` blocks in
`docker-compose.yml` and set `KAVITA_URL=http://host.docker.internal:5050` (Kavita's host port)
in `.env`. On Linux hosts also add `extra_hosts: ["host.docker.internal:host-gateway"]`.

## First sync and generating audio

- About 5 seconds after start, Lector syncs every Kavita library whose type is listed in
  `KAVITA_LIBRARY_TYPES` (default `2` = Book) and then re-syncs every `SYNC_INTERVAL_MINUTES`.
  Admins can trigger one any time with **Sync now** on the admin page. Books that disappear from
  Kavita are flagged as missing, never deleted, so audio and progress are kept.
- Nothing is narrated automatically. Open a book, pick a voice and speed, and start a narration
  (admins, or any user when "allow user requests" is on). Jobs are queued and processed one at
  a time; chapters become playable as soon as each one is finished.
- The first narration loads the Kokoro model (and downloads it if `/models` is empty), which
  takes a few seconds (or a few minutes for the download).

### How long does it take?

Kokoro on CPU runs at roughly **2-3x realtime** on an Apple M4 (all cores): one hour of audio
takes 20-30 minutes to generate, so a 15-hour novel takes about **5-7 hours** (a quick benchmark
inside Docker Desktop with 10 CPUs measured about 2.2x). Speed scales with the
CPU cores Docker can use (Docker Desktop -> Settings -> Resources). The admin page shows the
live realtime factor. For much faster generation, run Kokoro-FastAPI on a GPU machine and set
`TTS_ENGINE=openai`.

### Storage

At the default 64 kbps mono MP3, audio needs about **29 MB per hour**: a 15-hour novel is about
430 MB, a 100-book library at 10 h each is about 29 GB. An M4B export roughly doubles a book's
footprint until you delete it. Everything lives under `LECTOR_DATA_PATH`.

## Path mapping (`KAVITA_PATH_MAP`)

Kavita reports every file by its path **inside the Kavita container** (for example
`/books/Austen/Pride and Prejudice.epub`). Lector opens the same file through its own mount, so
it needs to know how Kavita's paths translate into Lector's:

```
KAVITA_PATH_MAP=/books=/books                 # default: both containers mount the folder at /books
KAVITA_PATH_MAP=/books=/books;/novels=/mnt/novels   # several Kavita roots, ';'-separated
```

Each entry is `kavitaPrefix=lectorPrefix`; the longest matching prefix wins. With the default
compose file `BOOKS_PATH` must be the host folder that Kavita mounts as `/books` (check the
`volumes:` of the Kavita service). If a mapped file does not exist, Lector falls back to
downloading a private copy through Kavita's API into `/data/cache/books` (the Kavita user needs
the **Download** role). Books that can be neither found nor downloaded show as missing.

## Configuration

Every setting is an environment variable; `.env.example` documents all of them with defaults.
The most important:

| Variable | Default | Purpose |
|---|---|---|
| `KAVITA_URL` | `http://kavita:5000` | Kavita base URL as seen from the container |
| `KAVITA_API_KEY` | | Kavita API key (preferred auth) |
| `KAVITA_USERNAME` / `KAVITA_PASSWORD` | | alternative auth |
| `KAVITA_LIBRARY_TYPES` | `2` | library types to import (2 Book, 4 Light Novel) |
| `KAVITA_PATH_MAP` | `/books=/books` | Kavita path -> Lector path |
| `SYNC_INTERVAL_MINUTES` | `60` | automatic sync interval, 0 = off |
| `TTS_ENGINE` | `kokoro` | `kokoro` or `openai` |
| `KOKORO_MODEL` / `KOKORO_VOICES` | `/models/kokoro-v1.0.onnx`, `/models/voices-v1.0.bin` | model files |
| `KOKORO_AUTO_DOWNLOAD` | `true` | download missing model files |
| `TTS_THREADS` | `0` | onnxruntime threads (0 = all cores) |
| `TTS_OPENAI_URL` / `TTS_OPENAI_KEY` / `TTS_OPENAI_MODEL` | `http://localhost:8880/v1`, `not-needed`, `kokoro` | OpenAI-compatible engine |
| `DEFAULT_VOICE` / `DEFAULT_SPEED` | `af_heart` / `1.0` | defaults (overridable in the admin UI) |
| `AUDIO_FORMAT` / `AUDIO_BITRATE` | `mp3` / `64k` | chapter encoding (`mp3` or `m4a`) |
| `LECTOR_SECRET_KEY` | auto | session-signing key, stored in `/data/secret.key` if unset |
| `ALLOW_REGISTRATION` | `false` | open sign-up |
| `ALLOW_USER_REQUESTS` | `true` | non-admins may queue narrations |
| `SESSION_DAYS` | `30` | login lifetime |
| `COOKIE_SECURE` | `false` | set `true` behind HTTPS |
| `LECTOR_DATA_DIR` / `LECTOR_AUDIO_DIR` / `LECTOR_DB_PATH` | `/data`, `/data/audio`, `/data/lector.db` | container paths |
| `PUID` / `PGID` | | run as this uid/gid (empty = root) |
| `LOG_LEVEL` | `INFO` | logging level |

Docker-only (host side): `LECTOR_PORT` (5055), `LECTOR_DATA_PATH`, `BOOKS_PATH`,
`KOKORO_MODELS_PATH`, `TZ`.

## Updating

```sh
git pull                      # or copy the new source over
docker compose up -d --build  # rebuilds the image and restarts; data in /data is kept
docker image prune -f         # optional: drop the old image
```

Database tables are created automatically at startup.

## Backups

All state is in `LECTOR_DATA_PATH` (`/data` in the container):

- `lector.db` - users, books, narration status, progress, bookmarks, settings (small; the
  important part)
- `secret.key` - session-signing key (losing it just logs everyone out)
- `audio/` - generated chapters and M4B files (large, but can be regenerated)
- `cache/` - covers, voice previews, downloaded book copies (disposable)

For a consistent copy of the database while running:

```sh
docker exec lector python -c "import sqlite3; s=sqlite3.connect('/data/lector.db'); d=sqlite3.connect('/data/lector-backup.db'); s.backup(d)"
```

or simply stop the container (`docker compose stop`) and copy the folder.

## Troubleshooting

- **Kavita unreachable** (admin page shows Kavita "not reachable"): check
  `docker exec lector python -c "import urllib.request;print(urllib.request.urlopen('http://kavita:5000/api/health').status)"`.
  If the name does not resolve, Lector is not on Kavita's network: either confirm the network
  name with `docker network ls` (and update `docker-compose.yml`), or switch to
  `KAVITA_URL=http://host.docker.internal:5050`. A `401`/"login failed" error means a wrong API
  key or password.
- **No books after a sync**: the library type is probably not listed in `KAVITA_LIBRARY_TYPES`
  (e.g. light novels are type `4`), or the library holds only PDFs/comics. The admin page shows
  the last sync's message.
- **Books marked missing / "file not found"**: the path mapping is wrong. Compare Kavita's mount
  (`docker inspect kavita --format '{{json .Mounts}}'`) with `BOOKS_PATH`, and run
  `docker exec lector ls /books` to confirm the files are visible. Fix `BOOKS_PATH` or
  `KAVITA_PATH_MAP`, then **Sync now**.
- **Model download fails** (no internet, GitHub blocked): download
  `kokoro-v1.0.onnx` and `voices-v1.0.bin` from
  <https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0> into
  `KOKORO_MODELS_PATH` yourself. If the folder is not writable for `PUID`, the download fails
  with a permission error.
- **Permission denied on /data**: create the folder before the first start and/or set
  `PUID`/`PGID` to its owner. The entrypoint chowns `/data` and `/models` when their owner does
  not match.
- **Generation is slow / the host is sluggish**: give Docker Desktop more CPUs, or cap Lector
  with `TTS_THREADS` or the commented `deploy.resources.limits` in `docker-compose.yml`.
- **Logs**: `docker compose logs -f lector` (set `LOG_LEVEL=DEBUG` for more detail).

## Local development

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt pytest
brew install ffmpeg            # or apt-get install ffmpeg

export LECTOR_DATA_DIR=./data KOKORO_MODEL=./models/kokoro-v1.0.onnx KOKORO_VOICES=./models/voices-v1.0.bin
export KAVITA_URL=http://localhost:5050 KAVITA_API_KEY=... KAVITA_PATH_MAP=/books=/path/to/books
uvicorn app.main:app --reload --port 5000 --workers 1

pytest -q                      # run the test suite
python -m app.epub_text book.epub   # print the chapter plan Lector would narrate for an EPUB
```

Build the image alone with `docker build -t lector:local .`.

## API

The JSON API (auth, library, renditions, progress, bookmarks, admin) is documented in
[`docs/API.md`](docs/API.md). FastAPI's interactive docs are normally available at `/docs` on a running server.
