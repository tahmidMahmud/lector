# Developing Lector

How to run Lector from source, test it, and extend it. Read
[`ARCHITECTURE.md`](ARCHITECTURE.md) first for how the pieces fit together, and
[`CONFIGURATION.md`](CONFIGURATION.md) for every setting.

## Project layout

```
lector/
├── app/
│   ├── __init__.py
│   ├── main.py              FastAPI app: lifespan (worker + scheduler), routers, static files, SPA fallback
│   ├── config.py            environment variables -> `settings`; path map parsing; secret key file
│   ├── db.py                SQLAlchemy models, SQLite pragmas, `init_db`, `session_scope`, `get_db`
│   ├── auth.py              bcrypt hashing, signed session tokens, `current_user` / `admin_user`
│   ├── schemas.py           Pydantic request bodies and validation limits
│   ├── kavita.py            Kavita REST client, pagination, sentinel handling, `file_entries`
│   ├── sync.py              Kavita -> `books` upsert, covers, path mapping/download, `SyncScheduler`
│   ├── epub_text.py         EPUB -> sections: blocks, Gutenberg trim, chapters, TTS cleanup, chunking; CLI
│   ├── worker.py            generation queue thread, chapter narration, ffmpeg encode, M4B export
│   ├── tts/
│   │   ├── __init__.py      `get_engine()` factory (singleton chosen by TTS_ENGINE)
│   │   ├── base.py          `TTSEngine` interface, Kokoro voice-id metadata (`describe_voice`)
│   │   ├── kokoro_engine.py built-in Kokoro-82M via kokoro-onnx; model download; thread setup
│   │   └── openai_engine.py OpenAI-compatible /v1/audio/speech client
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── _helpers.py      runtime settings, permission helpers, JSON serializers, live progress blend
│   │   ├── auth.py          /api/setup, /api/auth/*
│   │   ├── library.py       /api/library, books, covers, voices + previews, renditions + job control
│   │   ├── media.py         chapter audio (Range), timelines, M4B build/download
│   │   ├── progress.py      progress (incl. sendBeacon alias) and bookmarks
│   │   └── admin.py         /api/admin/*: status, sync, users, settings
│   └── static/
│       ├── index.html       app shell, pre-paint theme script, the single <audio> element
│       ├── app.js           the whole web client (router, api(), player, views), ES module
│       ├── app.css          styles, dark/light theme variables
│       ├── sw.js            service worker: static shell cache, never /api
│       ├── manifest.webmanifest  PWA manifest
│       └── icons/icon.svg   app icon
├── tests/
│   ├── test_epub_text.py    pipeline on real Gutenberg EPUBs (optional) and synthetic ones; text helpers
│   ├── test_kavita.py       Kavita client against an in-process fake (httpx transport)
│   ├── test_sync.py         sync into a temp SQLite with a fake client and generated EPUBs
│   ├── test_tts.py          voice metadata; Kokoro with the real model when available
│   └── test_worker.py       worker control flow with a fake engine; ffmpeg encode and M4B
├── docs/                    API.md, ARCHITECTURE.md, CONFIGURATION.md, DEVELOPMENT.md and the user/ops guides
├── scripts/entrypoint.sh    container entrypoint: PUID/PGID via gosu, ownership fix
├── Dockerfile               python:3.12-slim-bookworm + ffmpeg, libsndfile1, gosu
├── docker-compose.yml       single `lector` service on the Kavita network
├── .env.example             every setting with comments
├── .dockerignore            keeps tests, docs, .env, data and models out of the image
├── pytest.ini               `pythonpath = .`, `testpaths = tests`
├── requirements.txt         pinned runtime dependencies
├── requirements-dev.txt     runtime + pytest
└── README.md
```

## Local setup

Requirements:

- **Python 3.12.** `kokoro-onnx` 0.5.0 and its onnxruntime dependency have no wheels for very
  new interpreters (there is none for 3.14), so use 3.12, the version the Docker image uses.
- **ffmpeg and ffprobe** on `PATH` (`brew install ffmpeg`, `apt-get install ffmpeg`). ffmpeg
  encodes every chapter, voice preview and M4B; ffprobe is only used for exact M4B chapter
  times and some tests.
- **espeak-ng** comes with the `espeakng-loader` wheel; `soundfile` wheels bundle libsndfile on
  macOS and common Linux platforms. Nothing else to install.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
```

### Model files

The Kokoro engine needs two files (about 350 MB together) from the kokoro-onnx release
`model-files-v1.0`:

```sh
mkdir -p models
curl -L -o models/kokoro-v1.0.onnx \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o models/voices-v1.0.bin \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

Alternatively point `KOKORO_MODEL`/`KOKORO_VOICES` at a writable location and leave
`KOKORO_AUTO_DOWNLOAD=true`; the first load downloads them. `models/` and `data/` are in
`.gitignore`.

## Running the server locally

All container paths default to `/data` and `/models`, so set them for a checkout. With Kavita
reachable on the host (port 5050 in the default Docker setup) and its books folder visible
locally:

```sh
export LECTOR_DATA_DIR="$PWD/data"
export KOKORO_MODEL="$PWD/models/kokoro-v1.0.onnx" KOKORO_VOICES="$PWD/models/voices-v1.0.bin"
export KAVITA_URL=http://localhost:5050
export KAVITA_API_KEY=<your Kavita API key>
export KAVITA_PATH_MAP=/books=<host folder that Kavita mounts as /books>
export LOG_LEVEL=DEBUG            # optional
uvicorn app.main:app --reload --port 5000 --workers 1
```

Open <http://localhost:5000> and create the admin account. Interactive API docs are at
<http://localhost:5000/api/docs>.

Notes:

- Keep `--workers 1`: the queue, live status and engine are per process.
- `--reload` restarts the process on code changes. The worker is stopped (up to 20 s) and the
  rendition it was working on is requeued on the next start; the interrupted chapter is redone
  from its beginning.
- Without a Kavita you can still work on the worker and pipeline (see
  [Running the worker by hand](#running-the-worker-by-hand)). With `KAVITA_URL` unreachable the
  sync just records an error.
- If the files are not mounted at the mapped path, the sync downloads copies into
  `data/cache/books` (needs the Kavita Download role).
- To avoid the 5-second boot sync while iterating, set `SYNC_INTERVAL_MINUTES=0` and use
  **Sync now**.

## Tests

```sh
pytest            # or: pytest -q, pytest tests/test_worker.py -k pause
```

`pytest.ini` puts the repo root on `sys.path`. The modules that load the app's settings set
`LECTOR_DATA_DIR` to a temporary folder (with `setdefault`) before importing the app, and the
database fixtures monkeypatch `settings`
paths and call `db.init_db()` against a temp SQLite file, so tests never touch a real data
folder. The suite has 187 tests and runs in about 11 s with everything available.

| Module | Covers | Needs |
|---|---|---|
| `test_epub_text.py` | Gutenberg trimming, no hard-wrapped paragraphs, section sanity and specific chapter plans on real books; synthetic EPUBs (TOC, heading fallback, missing `container.xml`, not-a-zip); `heading_for_speech`, `smart_title`, `display_title`, `clean_for_tts`, `split_sentences`, `chunk_for_tts`; heading de-duplication and `text_hash` | real-book tests need the sample folder (below), the rest run anywhere |
| `test_kavita.py` | `is_sentinel`, `file_entries` ordering/labels/de-duplication; client against an in-process fake Kavita: API-key and password login, re-login on 401, pagination with and without the header, non-JSON and error responses, timeouts, retries on 503, URL validation, cover and download (no partial file left) | nothing |
| `test_sync.py` | one Book per file, OPF fallback for writers/summary, per-volume covers, idempotency, cover refetch, missing flagging and restore, failed series/library leave books alone, unreachable Kavita, download fallback, busy lock, scheduler start/stop | nothing (uses a fake client and generated EPUBs) |
| `test_tts.py` | `describe_voice` and language codes valid for espeak-ng; engine defaults; Kokoro not ready without files; real synthesis, voice validation, speed clamp/lang fix, `TTS_THREADS` session | real-model tests need the model files |
| `test_worker.py` | full job, queue + live status reset, pause mid-chapter, resume skipping done chapters, cancel, stop + requeue, ASCII fallback, silenced bad chunk, hard engine failure, one bad chapter, engine not ready, missing/non-EPUB file, heading-only and empty sections, re-narration on text change, restructured book, encode tags/duration, ffmpeg errors, unknown format fallback, M4B for mp3 and m4a | ffmpeg/ffprobe for the encoding tests; uses `FakeEngine` (a 50 ms sine per chunk), never the real model |

Optional resources, and the tests skip (not fail) without them:

- **Real books:** `LECTOR_TEST_BOOKS` (default `tests/fixtures/books`; point it at your
  Kavita books folder) searched recursively for `*.epub`. The specific-book tests look
  for *The Gift of the Magi*, *A Tale of Two Cities* and *The Works of Edgar Allan Poe -
  Volume 1* (all Project Gutenberg) and skip individually if a title is absent. Every book in
  the folder must be a Gutenberg EPUB with both markers, since the parametrized tests assert
  that.
- **Kokoro model:** `KOKORO_MODEL` / `KOKORO_VOICES`, falling back to a default path in the
  author's home folder.
- **ffmpeg/ffprobe** on `PATH`.

```sh
LECTOR_TEST_BOOKS=~/Books KOKORO_MODEL=$PWD/models/kokoro-v1.0.onnx \
  KOKORO_VOICES=$PWD/models/voices-v1.0.bin pytest -rs     # -rs lists skip reasons
```

There is no HTTP-level test module yet. For one, use FastAPI's `TestClient(app)` **without** a
`with` block (a `with` block runs the lifespan, which starts the worker and sync threads), point
`settings` at a temp folder as the existing fixtures do, and call `db.init_db()` yourself.

### Running the worker by hand

To narrate an EPUB end to end with the real engine, without Kavita or the web server, insert a
Book and a Rendition and call `worker.run_job()`. Save this as `narrate_one.py` anywhere (not
in `app/`) and run it from the repo root:

```python
"""Narrate one EPUB end to end.  PYTHONPATH=. python narrate_one.py book.epub [voice] [speed]"""
import logging
import os
import sys

# app.config reads the environment at import time, so set paths before importing app.*
os.environ.setdefault("LECTOR_DATA_DIR", os.path.abspath("data"))
os.environ.setdefault("KOKORO_MODEL", os.path.abspath("models/kokoro-v1.0.onnx"))
os.environ.setdefault("KOKORO_VOICES", os.path.abspath("models/voices-v1.0.bin"))

from sqlalchemy import select  # noqa: E402

from app.db import Book, Rendition, init_db, session_scope  # noqa: E402
from app.worker import rendition_dir, worker  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
epub = os.path.abspath(sys.argv[1])
voice = sys.argv[2] if len(sys.argv) > 2 else "af_heart"
speed = round(float(sys.argv[3]), 2) if len(sys.argv) > 3 else 1.0

init_db()
with session_scope() as s:
    # kavita_chapter_id is unique and NOT NULL; a negative id never collides with Kavita's.
    book = s.scalar(select(Book).where(Book.kavita_chapter_id == -1))
    if book is None:
        book = Book(kavita_series_id=-1, kavita_library_id=-1, kavita_chapter_id=-1,
                    series_title="Dev book", title="Dev book")
        s.add(book)
    book.local_path, book.file_format = epub, "epub"
    s.flush()
    r = s.scalar(select(Rendition).where(Rendition.book_id == book.id, Rendition.voice == voice,
                                         Rendition.speed == speed))
    if r is None:
        r = Rendition(book_id=book.id, voice=voice, speed=speed, engine="kokoro")
        s.add(r)
    # run_job() expects a job the queue already claimed: only a "running" rendition is
    # marked done at the end (this is what Worker._next_job() would do).
    r.status, r.error = "running", None
    s.flush()
    rid, book_id = r.id, book.id

worker.run_job(rid)  # blocks until done or error; Ctrl-C leaves it "running" (requeued on next start)

with session_scope() as s:
    r = s.get(Rendition, rid)
    print(f"rendition {rid}: {r.status} {r.error or ''} "
          f"({r.done_chapters}/{r.total_chapters} chapters, {r.total_duration / 60:.1f} min)")
    for c in r.chapters:
        print(f"  {c.index:3d} {c.status:8s} {c.duration:7.1f}s  {c.title}")
print("files in", rendition_dir(book_id, rid))
```

```sh
PYTHONPATH=. TTS_THREADS=8 python narrate_one.py ~/Books/gift-of-the-magi.epub af_heart 1.0
```

Running it again with the same voice and speed resumes: done chapters are skipped. To exercise
the real queue instead, set the status to `queued` and call `worker.start()`, then wait (the
thread is a daemon; `worker.stop(timeout=20)` before exiting). Because the script uses the same
`data/` folder, the rendition also shows up in a locally running server, under the "Dev book"
entry (a Kavita sync flags that row as missing, since Kavita does not know it; the script still
works).

### The EPUB debugging CLI

```sh
python -m app.epub_text book.epub          # chapter plan
python -m app.epub_text book.epub --dump   # plus the cleaned text of every narrated section
```

The first lines show title, authors, language, Gutenberg detection and whether the start/end
markers were found, then the number of sections, narrated characters and an hour estimate
(15 characters per second). Each section line has `SKIP` if skipped, its index, character count,
title and the first spoken paragraph:

```
A Tale of Two Cities — Charles Dickens [en]
gutenberg=True id=98 trimmed start=True end=True
46 sections, 747,875 narrated chars (~13.8 h)
SKIP   0   1,327  Contents                                           | Contents.
       1   5,779  CHAPTER I. The Period                              | A Tale of Two Cities.
       2  11,071  CHAPTER II. The Mail                               | Chapter 2. The Mail.
```

`--dump` prints exactly what goes into `chunk_for_tts` (after `clean_for_tts`), which is the
fastest way to check a cleanup rule. Remember that a plan change on an existing rendition makes
the worker rebuild (section count changed) or re-narrate (text hash changed) on its next run.

## Coding conventions

Followed throughout the code; keep to them.

- **Python.** `from __future__ import annotations`, type hints on public functions, a module
  docstring saying what the module is for, `logging.getLogger("lector.<area>")`. Deliberate
  broad `except Exception` carries `# noqa: BLE001` and a reason.
- **Short DB sessions.** Background code uses `with session_scope() as s:` (commit on success,
  rollback on error) for each small step and copies plain values out. Never hold a session while
  synthesizing, encoding, downloading or calling Kavita. Request handlers take
  `db: Session = Depends(get_db)` and call `db.commit()` explicitly; the dependency does not
  commit. `expire_on_commit=False`, so objects stay readable after commit.
- **Responses are dicts from serializers.** Request bodies are Pydantic models in
  `app/schemas.py` (`_Body`, `extra="ignore"`, limits as `Field` constraints); responses are
  plain dicts built by the functions in `app/routers/_helpers.py`. Do not hand-build the same
  object in two routers, and do not return ORM objects.
- **Errors.** Raise `HTTPException(status, "Human readable message")`; the client shows
  `detail` verbatim in a toast. Use `not_found()`, `get_book_or_404()`,
  `get_rendition_or_404()`, `require_requester()`.
- **Keep the event loop free.** Sync `def` endpoints run in the thread pool automatically. In
  `async def` endpoints, wrap anything blocking (engine, Kavita, file walks) in
  `run_in_threadpool`.
- **Atomic files.** Write to a `.part`/`.tmp` sibling and `replace()` it (chapters, timelines,
  covers, downloads, model files, M4B). Readers must never see half a file.
- **Secrets stay out of logs.** Never log URLs with `apiKey=` or tokens; `httpx` request logging
  is silenced on purpose in `app/main.py`.
- **Front end: no build step, no CDN, no dependencies.** Everything the browser loads is in
  `app/static/` and works on a LAN without internet. Build markup with the `html` tagged template
  (it escapes values; wrap trusted markup in `raw()`), make every request through `api()`, and
  register timers with `ctx.every()` / `ctx.onCleanup()` so they stop on navigation. Check
  `ctx.alive()` after every `await` in a view.
- **Docs move with the code.** An API change updates `docs/API.md`; a new setting updates
  `app/config.py`, `.env.example` and `docs/CONFIGURATION.md`.

## Adding an API endpoint, end to end

Example: `GET /api/books/{book_id}/plan`, returning the chapter plan the worker would narrate
(like the CLI), so the book page could show it before a narration is requested.

1. **Schema** (`app/schemas.py`): only needed for a request body. A `POST` variant would add
   e.g. `class PlanRequest(_Body): max_sections: int = Field(default=200, ge=1, le=1000)`.
2. **Serializer** (`app/routers/_helpers.py`):

   ```python
   def plan_dict(bt: "BookText") -> dict:
       return {"title": bt.title, "is_gutenberg": bt.is_gutenberg, "char_count": bt.char_count,
               "sections": [{"index": i, "title": s.title, "skipped": s.skipped,
                             "char_count": s.char_count} for i, s in enumerate(bt.sections)]}
   ```

3. **Route** (`app/routers/library.py`, next to the other book routes). Parsing a book takes
   up to a few seconds, so keep it off the event loop:

   ```python
   @router.get("/books/{book_id}/plan")
   async def book_plan(book_id: int, _: User = Depends(current_user),
                       db: Session = Depends(get_db)) -> dict:
       b = get_book_or_404(db, book_id)
       if b.missing or not b.local_path or b.file_format != "epub":
           raise HTTPException(status.HTTP_409_CONFLICT, "No readable EPUB for this book")
       bt = await run_in_threadpool(extract_book, b.local_path)
       return plan_dict(bt)
   ```

   (`extract_book` comes from `..epub_text`, `plan_dict` from `._helpers`.) Pick the auth
   dependency deliberately: `current_user`, `admin_user`, or `current_user` plus
   `require_requester(db, user)`. Routes in a new module need `app.include_router(...)` in
   `app/main.py` (the loop over modules), **before** the `/api/{rest:path}` catch-all, which is
   registered after the routers and would otherwise answer 404.
4. **Client** (`app/static/app.js`): call it through the helper, inside the view, and respect
   `ctx.alive()`:

   ```js
   const plan = await api(`/api/books/${encodeURIComponent(bookId)}/plan`, { quiet: true }).catch(() => null);
   if (!ctx.alive()) return;
   ```

5. **Docs**: add a row to the right table in `docs/API.md` (method, path, body, returns, notes)
   and describe any new object under "Objects".
6. **Test**: unit-test the serializer or helper directly; for the route, see the `TestClient`
   note under [Tests](#tests).

## Adding a TTS engine

1. Create `app/tts/<name>_engine.py` with a subclass of `TTSEngine` (`app/tts/base.py`):
   - `name` (stored on renditions) and `sample_rate`;
   - `voices()` returning `[{id, name, language, gender}]` (use `describe_voice(id)` if the ids
     follow Kokoro's scheme);
   - `synthesize(text, voice, speed, lang)` returning **float32 mono** samples at `sample_rate`,
     raising on failure (the worker retries with ASCII text, then inserts silence; three failures
     in a row fail the chapter);
   - `ready()` returning `(ok, message)` cheaply and repeatably (it is called at worker start, per
     job, by the admin status page and before previews);
   - optionally `warmup()` and `has_voice()` (return `None` if you cannot tell).
2. Keep `synthesize` safe to call from two threads (the worker and a voice preview), with a lock
   if the backend is not thread-safe, as `KokoroEngine` does.
3. Add the settings to `app/config.py` and `.env.example`, and a branch in `get_engine()`
   (`app/tts/__init__.py`), importing the module lazily so its dependencies are only needed
   when selected.
4. Voice ids must match `^[A-Za-z0-9_.-]{1,64}$` (`VOICE_ID_RE` in `app/routers/library.py`); they
   are used in file names for previews. Display names come from `describe_voice()` in
   `rendition_dict`, so non-Kokoro ids show generic metadata unless you extend it.
5. Chunks are at most 380 characters. If the engine has a smaller input limit, pass a lower
   `max_chars` to `chunk_for_tts` in `Worker._narrate_section`.
6. Add tests in `tests/test_tts.py`, skipping when the backend is unavailable, and document the
   engine in `docs/CONFIGURATION.md`.

## Extending the text pipeline (for example PDFs)

The worker needs a `BookText` (title, authors, language, and `Section`s with `title`,
`paragraphs`, `headings`, `skipped`). Everything after that (cleanup, chunking, timelines,
resume by `text_hash`) is format-independent.

1. Write `extract_pdf(path) -> BookText` in a new module (for example `app/pdf_text.py`) with a
   PDF library added to `requirements.txt`. Use the PDF outline for chapter starts where it
   exists, fall back to pages or size, rejoin hyphenated line breaks, and produce paragraphs
   without hard line wraps (`chunk_for_tts` treats `\n` as a boundary).
2. Reuse the shared pieces rather than copying them: the Gutenberg marker scan is inline in
   `extract_book()`; factor it into a function over a list of text blocks before using it twice.
   `MIN_CHAPTER_CHARS`/`MAX_CHAPTER_CHARS` merging and splitting should apply too.
3. Dispatch on the file suffix in `Worker._process` (`app/worker.py`), which currently rejects
   anything but `.epub` ("Only EPUB books can be narrated right now").
4. The sync already imports PDFs from Book libraries (`file_format = "pdf"`); only the OPF
   metadata fallback and Gutenberg detection are EPUB-specific.
5. Give the new extractor a `python -m` entry point like `app.epub_text`, and tests with a small
   generated PDF.

Smaller pipeline changes (a new cleanup rule, a skip title) go into `app/epub_text.py` with a
parametrized case in `tests/test_epub_text.py`. Changes to `clean_for_tts` affect only newly
narrated chapters; changes that alter `spoken_paragraphs()` change `text_hash` and make existing
renditions re-narrate those chapters when they are next resumed or retried.

## Database changes

`init_db()` only runs `Base.metadata.create_all()`, which creates missing **tables** but never
alters existing ones. A new table needs nothing else. A new column on an existing table needs an
explicit migration for existing installs, for example in `init_db()` after `create_all`:

```python
with _engine.begin() as conn:
    cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(renditions)")}
    if "priority" not in cols:
        conn.exec_driver_sql("ALTER TABLE renditions ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
```

Keep migrations additive and idempotent; there is no downgrade path.

## Docker

```sh
docker build -t lector:local .
docker compose up -d --build
docker compose logs -f lector
```

- **Base and contents.** `python:3.12-slim-bookworm`, plus `ffmpeg`, `libsndfile1`, `gosu` and
  `ca-certificates` from Debian, the pinned `requirements.txt`, `app/`, and the entrypoint.
  `.dockerignore` keeps `tests/`, `docs/`, `*.md`, `.env*`, `data/` and `models/` out of the
  build context. The image is about 1.2 GB uncompressed, mostly ffmpeg and its libraries,
  onnxruntime and numpy. The Kokoro model is not in the image; it lives on the `/models` volume.
- **Volumes and port.** `/data` and `/models` are declared volumes; `/books` is created empty
  for the read-only mount. The app listens on 5000.
- **Entrypoint** (`scripts/entrypoint.sh`). Creates `$LECTOR_DATA_DIR`. If the container runs
  as root and `PUID` is set, it changes the built-in `lector` user/group (uid/gid 1000) to
  `PUID`/`PGID` (`usermod -o`/`groupmod -o`, so ids already taken, like macOS's group 20, are
  fine), chowns `/data` and the models folder recursively only when their top-level owner does
  not match (so a large audio library is not re-chowned on every start; failures only warn), and
  `exec gosu PUID:PGID` the command. Without `PUID` the command runs unchanged, as root.
- **Command.** `uvicorn app.main:app --host 0.0.0.0 --port 5000 --workers 1`. Never raise the
  worker count.
- **Healthcheck.** Every 30 s, `GET http://127.0.0.1:5000/api/health` must answer within 4 s;
  60 s start period, 3 retries. It only shows that the web server responds, not that Kavita or
  the engine work (the admin page shows those).
- `docker exec lector ...` runs as root (it bypasses the entrypoint); files created that way in
  `/data` may need a `chown`.

## Release checklist

1. `pytest -rs` with the real books, the Kokoro model and ffmpeg available, so nothing is
   skipped.
2. `python -m app.epub_text` on a few representative books (Gutenberg and not) if the pipeline
   changed; compare section counts with the previous version, because a different plan makes
   existing renditions start over.
3. Narrate a short book end to end (the script above or the UI), play it in a phone browser,
   check seeking, read-along and an M4B export.
4. If a column was added, check the migration on a copy of a real `lector.db`.
5. Update `APP_VERSION` in `app/routers/_helpers.py` (shown by `GET /api/setup` and the OpenAPI
   page).
6. Update `docs/API.md`, `docs/CONFIGURATION.md`, `.env.example` and `README.md` for any changed
   endpoint, setting or default.
7. If the list of shell files in `app/static/sw.js` changed, bump `CACHE` (`lector-shell-v1`) so
   old caches are dropped. Content changes alone do not need it: the service worker is
   network-first.
8. `docker compose up -d --build` on a test install; confirm the container becomes `healthy`, a
   sync completes and an interrupted job resumes after `docker compose restart`.
9. Commit, tag (`git tag v<version>`), push.
