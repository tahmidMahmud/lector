# Lector configuration reference

Lector is configured with environment variables, read once at import time by
`app/config.py` (`settings = Settings()`). With the bundled `docker-compose.yml` they come from
`.env` (`env_file: .env`), which also supplies the host-side values used for variable
substitution in the compose file. Copy `.env.example` to `.env` to start; empty values fall back
to the defaults. Four settings can additionally be changed at runtime in the admin UI (see
[Runtime settings](#runtime-settings)).

Parsing rules (helpers in `app/config.py`):

- **bool** (`_bool`): unset or empty means the default; otherwise only `1`, `true`, `yes`, `on`
  (any case) are true. Anything else, including `y` or `enabled`, is **false**.
- **int** (`_int`): unset, empty or not a number means the default.
- `KAVITA_LIBRARY_TYPES`, `DEFAULT_SPEED` and `LOG_LEVEL` are parsed without a fallback: an
  invalid value stops the app at startup with a Python error.

Changing a variable requires a restart (`docker compose up -d` recreates the container when
`.env` changed).

## Environment variables

### Container paths

| Variable | Default | Type | What it does | When to change |
|---|---|---|---|---|
| `LECTOR_DATA_DIR` | `/data` | path | Root of all state: database, secret key, `cache/` (covers, downloaded books, voice previews), and audio unless `LECTOR_AUDIO_DIR` is set. Set to `/data` by `docker-compose.yml` and the `Dockerfile` | Local development (e.g. `./data`) |
| `LECTOR_AUDIO_DIR` | `$LECTOR_DATA_DIR/audio` | path | Chapter audio and M4B files (`<book_id>/<rendition_id>/`) | To put audio on a different volume than the database (add a matching mount) |
| `LECTOR_DB_PATH` | `$LECTOR_DATA_DIR/lector.db` | path | SQLite file (WAL mode, so `-wal`/`-shm` files sit next to it). Must be on a local filesystem | Rarely; never on a network share |

The cache folder is always `$LECTOR_DATA_DIR/cache` and the secret key file always
`$LECTOR_DATA_DIR/secret.key`.

### Kavita

| Variable | Default | Type | What it does | When to change |
|---|---|---|---|---|
| `KAVITA_URL` | `http://kavita:5000` | URL | Kavita base URL as seen from inside the Lector container; a trailing `/` is removed. Must start with `http://` or `https://` | Kavita not on the same Docker network: `http://host.docker.internal:5050` (Docker Desktop), or the LAN address |
| `KAVITA_API_KEY` | empty | string | Preferred auth. Exchanged for a JWT through `POST /api/Plugin/authenticate`. In Kavita: user menu -> Settings -> 3rd Party Clients | Always set this or the username/password pair |
| `KAVITA_USERNAME` | empty | string | Alternative auth, used only when `KAVITA_API_KEY` is empty (`POST /api/Account/login`) | If you cannot get an API key |
| `KAVITA_PASSWORD` | empty | string | Password for `KAVITA_USERNAME` | |
| `KAVITA_LIBRARY_TYPES` | `2` | comma-separated ints | Kavita library types to import (table below) | Add `4` for Light Novel libraries |
| `KAVITA_PATH_MAP` | `/books=/books` | `src=dst;...` | Translates Kavita's file paths to paths inside Lector ([details](#kavita_path_map)) | When Kavita and Lector mount the books at different paths |
| `SYNC_INTERVAL_MINUTES` | `60` | int | Minutes between automatic syncs, measured from the end of the previous one. The first sync runs about 5 s after start. `0` or negative disables the scheduler entirely, including the first sync; **Sync now** on the admin page still works | Larger libraries or a busy Kavita: increase. Sync only on demand: `0` |

Library types (Kavita's `LibraryType` enum, as listed in `.env.example`):

| Value | Kavita type | Narratable? |
|---|---|---|
| 0 | Manga | no (images) |
| 1 | Comic | no (images) |
| 2 | Book | yes, EPUB files |
| 3 | Image | no |
| 4 | Light Novel | yes, EPUB files |

Image-based libraries are listed by Kavita but contain nothing Lector can read. PDFs in Book
libraries are imported (`file_format = pdf`) but narrating them fails with "Only EPUB books can
be narrated right now".

### Text-to-speech

| Variable | Default | Type | What it does | When to change |
|---|---|---|---|---|
| `TTS_ENGINE` | `kokoro` | `kokoro` or `openai` | `kokoro`: built-in Kokoro-82M via kokoro-onnx on the CPU. `openai`: any OpenAI-compatible `/v1/audio/speech` server. Case-insensitive; any value other than `openai` means `kokoro` | GPU server available: `openai` |
| `KOKORO_MODEL` | `/models/kokoro-v1.0.onnx` | path | Kokoro ONNX model (about 325 MB). Set by compose; the entrypoint also uses its folder for the ownership fix | Local development |
| `KOKORO_VOICES` | `/models/voices-v1.0.bin` | path | Kokoro voice pack (about 28 MB, 54 voices) | Local development |
| `KOKORO_AUTO_DOWNLOAD` | `true` | bool | Download missing model files from the kokoro-onnx GitHub release on first use. The folder must be writable by `PUID` | Offline hosts: `false` and place the files yourself |
| `TTS_THREADS` | `0` | int | onnxruntime intra-op threads. `0` = onnxruntime's default ([guidance](#tts_threads)) | To leave CPU for other services, or to tune speed |
| `TTS_OPENAI_URL` | `http://localhost:8880/v1` | URL | Base URL of the OpenAI-compatible server, including `/v1`; trailing `/` removed. Used only with `TTS_ENGINE=openai` | Always when using `openai`; `localhost` is the Lector container itself |
| `TTS_OPENAI_KEY` | `not-needed` | string | Sent as `Authorization: Bearer <key>` | If your server checks keys |
| `TTS_OPENAI_MODEL` | `kokoro` | string | `model` field of speech requests | Server-specific, e.g. `tts-1` for openedai-speech |
| `DEFAULT_VOICE` | `af_heart` | voice id | Voice preselected for new narrations (runtime setting `default_voice`) | Taste; overridable in the admin UI |
| `DEFAULT_SPEED` | `1.0` | float | Speed used when a request does not name one (runtime setting `default_speed`). The API accepts 0.5-2.0 and Kokoro clamps to that range; a value outside it here is stored on the rendition as given but synthesized at the clamped speed | Taste; overridable in the admin UI |
| `AUDIO_FORMAT` | `mp3` | `mp3` or `m4a` | Codec of chapter files: MP3 (libmp3lame) or AAC in `.m4a`. Any other value logs a warning and uses `mp3` | `m4a` for smaller files or faster M4B export ([trade-offs](#audio-format-and-bitrate)) |
| `AUDIO_BITRATE` | `64k` | ffmpeg bitrate | Passed verbatim to ffmpeg `-b:a` for chapters, voice previews and the AAC re-encode of M4B exports | See the size table |

### Users and sessions

| Variable | Default | Type | What it does | When to change |
|---|---|---|---|---|
| `LECTOR_SECRET_KEY` | empty | string | Key that signs session tokens. Empty: read `$LECTOR_DATA_DIR/secret.key`, or generate one there ([details](#secret-key)) | To manage the key yourself (e.g. from a secrets store) |
| `ALLOW_REGISTRATION` | `false` | bool | Anyone can create a (non-admin) account from the login page (runtime setting `allow_registration`) | Rarely; prefer admin-created accounts |
| `ALLOW_USER_REQUESTS` | `true` | bool | Non-admin users may request narrations and build M4Bs (runtime setting `allow_user_requests`) | `false` to keep the CPU queue under admin control |
| `SESSION_DAYS` | `30` | int | Login lifetime: cookie `Max-Age` and the token's maximum age, checked on every request with the current value (so lowering it also shortens existing sessions) | Shorter on shared devices |
| `COOKIE_SECURE` | `false` | bool | Adds the `Secure` flag to the session cookie | `true` when served over HTTPS ([details](#cookies-and-https)) |

### Misc

| Variable | Default | Type | What it does | When to change |
|---|---|---|---|---|
| `LOG_LEVEL` | `INFO` | Python level name | Root log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). `httpx`/`httpcore` stay at WARNING and `phonemizer` at ERROR regardless | `DEBUG` when diagnosing sync or TTS problems |

### Compose and entrypoint only

These are not read by the Python code. They are used by `docker-compose.yml` (substitution) or
by `scripts/entrypoint.sh` inside the container.

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `LECTOR_PORT` | `5055` | compose | Host port mapped to the container's port 5000 |
| `LECTOR_DATA_PATH` | required | compose | Host folder mounted at `/data`. Create it before the first start, on a disk with room for audio |
| `BOOKS_PATH` | required | compose | Host folder mounted read-only at `/books`; must be the folder Kavita mounts as `/books` (or adjust `KAVITA_PATH_MAP`) |
| `KOKORO_MODELS_PATH` | required (`.env.example`: `./models`) | compose | Host folder mounted at `/models`; writable if the model files still need to be downloaded |
| `PUID` | empty (`.env.example`: `501`) | entrypoint | Run the app as this uid via `gosu`. Empty: run as root |
| `PGID` | `$PUID` (`.env.example`: `20`) | entrypoint | Group id for the app process |
| `TZ` | unset (`.env.example`: `America/Chicago`) | container | Time zone of log timestamps. The database always stores UTC and the API returns UTC |

With `PUID` set, the entrypoint re-points the image's `lector` user to `PUID`/`PGID` (duplicate
ids are allowed, e.g. macOS group 20), runs `chown -R` on `/data` and the models folder only if
the top-level owner differs, and then `exec gosu PUID:PGID uvicorn ...`. The books are mounted
read-only, so the host files must be readable by that uid.

## Runtime settings

Four values can be changed by an admin on the admin page (`PUT /api/admin/settings`):

| Key | Env default | Effect |
|---|---|---|
| `default_voice` | `DEFAULT_VOICE` | Voice the book page preselects (after the user's own preferred voice). Validated against the engine's voice list when saved |
| `default_speed` | `DEFAULT_SPEED` | Speed used when a narration request omits `speed`; 0.5-2.0, rounded to two decimals |
| `allow_user_requests` | `ALLOW_USER_REQUESTS` | Non-admins may request narrations and build M4Bs |
| `allow_registration` | `ALLOW_REGISTRATION` | Open sign-up on the login page |

How they override the environment (`effective_settings()` in `app/routers/_helpers.py`):

- A value saved in the UI is stored JSON-encoded in the `settings` table and wins over the
  environment from then on. Changing the environment variable later has no effect on that key.
- A key never saved in the UI follows the environment variable.
- Values are read from the database on every request, so changes apply immediately, without a
  restart.
- There is no "reset to default" button. To hand a key back to the environment variable,
  delete its row (no restart needed):

```sh
docker exec lector python -c "import sqlite3; c=sqlite3.connect('/data/lector.db'); c.execute(\"DELETE FROM settings WHERE key='default_voice'\"); c.commit()"
```

## `KAVITA_PATH_MAP`

Kavita reports each file by the path it sees inside its own container. Lector must open the
same file through its own mounts. `KAVITA_PATH_MAP` is a `;`-separated list of
`kavitaPrefix=lectorPrefix` pairs:

- whitespace around each pair is trimmed (not around the `=`), entries without `=` are ignored,
  trailing `/` are removed from both sides;
- the longest matching prefix wins;
- a prefix matches the whole path or a path continuing with `/` (`/books` matches `/books` and
  `/books/x.epub`, not `/bookshelf/x.epub`);
- matching is a plain, case-sensitive string comparison; backslashes are not converted;
- a path that matches no prefix is used unchanged.

If the mapped file is not readable, the sync downloads a private copy through Kavita
(`/api/Download/chapter`, needs the Download role on the Kavita user) into
`/data/cache/books/<chapter_id>.epub`. If that fails too, the book is shown as missing. The log
line `cannot access <kavita path> (mapped <lector path>) and download failed: ...` (or
`downloaded <file> from Kavita (path <mapped> not mounted)`) shows both sides of a failed
mapping.

**Example 1: identity (the default).** Kavita's compose mounts `/srv/media/books:/books`, and
Lector's `.env` has `BOOKS_PATH=/srv/media/books`, so both containers see the files at `/books`:

```
KAVITA_PATH_MAP=/books=/books
Kavita: /books/Austen/Pride and Prejudice.epub -> Lector: /books/Austen/Pride and Prejudice.epub
```

**Example 2: Kavita uses other container paths.** Kavita mounts two folders,
`/srv/novels:/novels` and `/srv/classics:/data/classics`, while Lector's compose mounts
`/srv:/books:ro` (set `BOOKS_PATH=/srv`):

```
KAVITA_PATH_MAP=/novels=/books/novels;/data/classics=/books/classics
Kavita: /data/classics/Dickens/Bleak House.epub -> Lector: /books/classics/Dickens/Bleak House.epub
```

**Example 3: Kavita on Windows.** Kavita running natively on Windows reports drive paths. Check
the exact form in the log line above (Kavita normally uses forward slashes, e.g.
`C:/Media/Books/...`). With the share mounted into Lector at `/books`:

```
KAVITA_PATH_MAP=C:/Media/Books=/books
Kavita: C:/Media/Books/Poe/Poems.epub -> Lector: /books/Poe/Poems.epub
```

Case must match exactly (`c:/media/books` would not match). If Kavita reports backslashes
(`C:\Media\Books\...`) no prefix can match them, and Lector falls back to downloading every
file; that works (with the Download role) but uses disk space in `/data/cache/books`.

After changing the mapping, restart Lector and press **Sync now**; books flagged missing are
cleared when their files are found.

## Audio format and bitrate

Chapter files are mono, 24 kHz (Kokoro's native rate). Size depends only on the bitrate:

| `AUDIO_BITRATE` | MB per hour | 15-hour novel | 100 books x 10 h |
|---|---|---|---|
| `32k` | 14.4 | 216 MB | 14 GB |
| `48k` | 21.6 | 324 MB | 22 GB |
| `64k` (default) | 28.8 | 432 MB | 29 GB |
| `96k` | 43.2 | 648 MB | 43 GB |
| `128k` | 57.6 | 864 MB | 58 GB |

- **`mp3`** plays everywhere. 64 kbps mono is clean for speech; 48k is still fine for most
  listeners.
- **`m4a`** (AAC) sounds better than MP3 at the same low bitrate, so `48k` AAC is a reasonable
  space saver. If every chapter of a rendition is `.m4a`, the M4B export copies the audio
  without re-encoding, which is faster and lossless; with MP3 chapters the export re-encodes to
  AAC at `AUDIO_BITRATE`.
- An M4B export adds roughly the size of the rendition again, until the rendition is deleted.
- The setting applies to chapters encoded after the change. Existing files are kept; a
  rendition can end up with mixed formats, which plays fine (each chapter is served with its own
  media type).
- During generation, each chapter is first written as an uncompressed float WAV next to its
  final file (about 346 MB per hour of audio, up to about 290 MB for the longest chapters), so
  keep that much free space in `LECTOR_AUDIO_DIR`.

## `TTS_THREADS`

- `0` lets onnxruntime choose, which normally means using all the cores the container can see.
  Narration then saturates the CPU while a job runs.
- A positive value creates an onnxruntime session with that many intra-op threads (and one
  inter-op thread). If that fails, Lector logs a warning and uses the defaults.
- Measured on an Apple M4 with `TTS_THREADS=8`: about 5x realtime. More threads than physical
  performance cores rarely help; fewer leave room for Kavita and other services at the cost of
  speed, roughly in proportion.
- On Docker Desktop the container only sees the CPUs assigned in Settings -> Resources. A
  `deploy.resources.limits.cpus` cap in `docker-compose.yml` (commented example included) limits
  CPU without changing the thread count; set `TTS_THREADS` to match the cap to avoid
  oversubscription.
- The admin page shows the live realtime factor, which is the number to tune against.

## OpenAI-compatible engine

`TTS_ENGINE=openai` sends each chunk (up to 380 characters) to
`POST {TTS_OPENAI_URL}/audio/speech` as
`{"model": TTS_OPENAI_MODEL, "input": ..., "voice": ..., "speed": ..., "response_format": "wav"}`.
Readiness is `GET {TTS_OPENAI_URL}/models` (any status below 500 counts as ready), and the voice
list comes from `GET {TTS_OPENAI_URL}/audio/voices` (a Kokoro-FastAPI extension; without it
Lector offers five default Kokoro voice ids). Returned audio is mixed to mono and resampled to
24 kHz. Voice ids must match `^[A-Za-z0-9_.-]{1,64}$`; names, languages and genders in the UI are
derived from Kokoro-style ids (`af_heart`), so other naming schemes show as American English,
female.

[Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) serves the same Kokoro model on a
GPU and is the natural choice. Example service, added to Lector's `docker-compose.yml` (pin an
image version you have tested):

```yaml
services:
  kokoro-tts:
    image: ghcr.io/remsky/kokoro-fastapi-gpu:latest   # or kokoro-fastapi-cpu
    container_name: kokoro-tts
    restart: unless-stopped
    networks:
      - media-server_default        # the same network as lector
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

and in `.env`:

```
TTS_ENGINE=openai
TTS_OPENAI_URL=http://kokoro-tts:8880/v1
TTS_OPENAI_MODEL=kokoro
TTS_OPENAI_KEY=not-needed
```

If the TTS server runs on another machine, use its address (`http://192.168.1.20:8880/v1`).
Notes:

- The engine is chosen at process start. Renditions record the engine name when requested, but
  a resumed job always uses the engine configured now.
- The model files in `/models` are not needed with `openai` (the Kokoro engine is never
  loaded).
- Voice previews are cached by voice id in `/data/cache/previews`; delete that folder after
  switching engines if the previews should come from the new one.

## Secret key

Session tokens are signed with `itsdangerous` using the secret key, resolved on first use by
`Settings.load_or_create_secret()`:

1. `LECTOR_SECRET_KEY`, if set, is used verbatim.
2. Otherwise `$LECTOR_DATA_DIR/secret.key` is read.
3. Otherwise a random 64-character key (`secrets.token_urlsafe(48)`) is generated, written to
   that file and the file is made mode `0600`.

Changing or deleting the key logs every user out at the next restart, nothing else. Back up
`secret.key` together with the database if you want sessions to survive a restore. Do not set
`LECTOR_SECRET_KEY` to something short or guessable: anyone who knows it can mint a session for
any user id.

## Cookies and HTTPS

The session cookie is `lector_session`: HttpOnly, SameSite=Lax, path `/`, `Max-Age` of
`SESSION_DAYS`, and `Secure` only when `COOKIE_SECURE=true`.

- On plain HTTP (the default LAN setup) leave `COOKIE_SECURE=false`. With `true` over HTTP the
  browser drops the cookie, so login appears to work and the next request is `401`.
- Behind an HTTPS reverse proxy (Caddy, Traefik, nginx) set `COOKIE_SECURE=true`. Lector builds
  no absolute URLs, so no forwarded-header configuration is needed. Proxy WebSocket support is
  not required; do keep `Range` headers and response streaming intact for audio.
- HTTPS (or `localhost`) is also what lets the browser register the service worker, i.e. the
  installable, offline-capable app shell.
- API clients that cannot keep cookies can send `Authorization: Bearer <token>` with the
  `token` returned by `POST /api/auth/login`.
