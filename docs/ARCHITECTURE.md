# Lector architecture

This document describes how Lector is built: the processes and threads, the data model, the
Kavita sync, the EPUB-to-speech pipeline, the generation worker, the HTTP layer and the web
client. It is written against the code as it is; function and file names are given so you can
find each behaviour. For the HTTP contract see [`API.md`](API.md), for settings see
[`CONFIGURATION.md`](CONFIGURATION.md), and for working on the code see
[`DEVELOPMENT.md`](DEVELOPMENT.md).

## 1. Overview and goals

Lector turns the EPUBs in an existing Kavita library into chapterized audiobooks with a local
TTS model, and serves them to a mobile-friendly web player.

Design goals, in order:

- **LAN-first, self-hosted.** Nothing leaves the machine: text-to-speech runs locally
  (Kokoro-82M through `kokoro-onnx` on the CPU). The only outbound traffic is to Kavita and, on
  first use, the one-time model download from GitHub.
- **One container, one process.** FastAPI under uvicorn with exactly one worker process. The
  narration queue is a thread inside that process, the queue itself is a column in SQLite.
  There is no Redis, Celery, Postgres or separate worker container.
- **Restart-safe.** Every piece of progress is in SQLite or on disk. A restart requeues
  whatever was running and resumes at the first unfinished chapter.
- **Listen while it generates.** Audio is produced one file per chapter, so a chapter is
  playable as soon as it is encoded.
- **Kavita stays the source of truth.** Lector reads the library; it never writes to Kavita and
  never deletes a book on its own.

## 2. Components

```
                         +-----------------------------+
                         |           Kavita            |
                         |  REST API      /books files |
                         +------+---------------+------+
                                |               |
             JWT (API key or    |               | same host folder mounted read-only
             user/password)     |               | (or /api/Download/chapter fallback)
                                v               v
+---------------------------------------------------------------------------------------+
| lector container: one uvicorn process (--workers 1)                                    |
|                                                                                       |
|  app/kavita.py  KavitaClient ----> app/sync.py run_sync() ----+                       |
|      (thread: lector-sync every SYNC_INTERVAL_MINUTES,        |  upsert Book rows,     |
|       lector-sync-manual for "Sync now")                      |  cache covers          |
|                                                               v                       |
|                                              +--------------------------------+       |
|                                              | SQLite /data/lector.db (WAL)   |       |
|                                              | users books renditions         |       |
|                                              | chapters progress bookmarks    |       |
|                                              | settings sync_log              |       |
|                                              +---+------------------------+---+       |
|                     status='queued' rows         |                        ^           |
|                                                  v                        |           |
|  app/worker.py Worker (thread: lector-worker)                             |           |
|    extract_book()  app/epub_text.py   EPUB -> Sections                    |           |
|      -> clean_for_tts() -> chunk_for_tts()  (<= 380 chars)                |           |
|      -> TTSEngine.synthesize()  app/tts/  (Kokoro / OpenAI-compatible)    |           |
|      -> float32 WAV on disk -> ffmpeg -> NNN.mp3 + NNN.json   ------------+ chapter   |
|                                                  |                          rows      |
|                                                  v                                    |
|                         /data/audio/<book_id>/<rendition_id>/NNN.mp3|json             |
|                                                  |                                    |
|  FastAPI app/main.py + app/routers/*  <----------+   (thread: lector-m4b-<rid> builds |
|    /api/... JSON, Range audio, covers, M4B          audiobook.m4b on request)         |
|    /static/*, SPA fallback -> index.html                                              |
+---------------------------------+-----------------------------------------------------+
                                  |
                                  v
                 Browser / phone: app/static/app.js (hash-routed SPA, one <audio>)
```

### Threads

| Thread | Started by | Purpose |
|---|---|---|
| uvicorn event loop | uvicorn | async endpoints; there is exactly one process (`CMD` in `Dockerfile`) |
| AnyIO thread pool | FastAPI | plain `def` endpoints, and `run_in_threadpool` calls (voice listing, voice previews, Kavita/engine status) |
| `lector-worker` | `Worker.start()` from `lifespan` in `app/main.py` | the narration queue, one rendition at a time |
| `lector-sync` | `SyncScheduler.start()` | first sync 5 s after boot, then every `SYNC_INTERVAL_MINUTES`; not started at all when the interval is `<= 0` |
| `lector-sync-manual` | `POST /api/admin/sync` | one-off sync; shares the lock with the scheduler |
| `lector-m4b-<rid>` | `POST /api/renditions/{rid}/m4b` | builds one M4B; `Worker._m4b_lock` serializes builds |

Voice previews are not separate threads: `voice_preview` in `app/routers/library.py` runs
`_build_preview` in the AnyIO pool, serialized by `_preview_lock`. Kokoro synthesis itself is
serialized by `KokoroEngine._synth_lock`, so a preview waits for the worker's current chunk.

Why one process: the worker thread, the in-memory `LiveStatus`, the M4B "building" set
(`_m4b_building` in `app/routers/media.py`) and the engine singleton (`get_engine()` in
`app/tts/__init__.py`) all live in process memory. A second uvicorn worker would start a second
queue consumer and load a second copy of the model.

Shutdown: `lifespan` calls `worker.stop(timeout=20)` (sets the stop event; the job is abandoned
at the next chunk boundary) and `scheduler.stop()`. All background threads are daemons.

## 3. Data model

Defined in `app/db.py` (SQLAlchemy 2, declarative). `init_db()` creates missing tables with
`Base.metadata.create_all`; there is no migration framework, so adding a column to an existing
table needs a manual `ALTER TABLE` (see DEVELOPMENT.md). Every connection sets
`PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `synchronous=NORMAL`, with a 30 s busy timeout.
Datetimes are stored as naive UTC (`utcnow()`) and serialized with a trailing `Z` by `iso()` in
`app/routers/_helpers.py`.

```
users 1---* progress *---1 renditions *---1 books
  1                          1   1
  +---* bookmarks *----------+   +---* chapters
  +--(requested_by, SET NULL)----renditions
settings (key/value)      sync_log (one row per sync)
```

### `users`

| Column | Meaning |
|---|---|
| `id` | primary key |
| `username` | unique (DB, case-sensitive); the API also refuses case-insensitive duplicates (`username_taken`) |
| `password_hash` | bcrypt, 12 rounds |
| `is_admin` | admin role; the first account (`POST /api/setup`) is an admin |
| `created_at` | |
| `playback_rate` | default player speed (0.5 to 3.0) |
| `skip_back_seconds`, `skip_forward_seconds` | player skip buttons (defaults 15 / 30) |
| `preferred_voice` | voice preselected on the book page; null = server default |
| `theme` | `dark`, `light` or `auto` |

### `books`

One row per narratable **file** in Kavita, not per series. Kavita models a library as
series -> volumes -> chapters -> files. A single novel is one series with one file; a
multi-volume work ("The Works of Edgar Allan Poe", volumes 1 and 2) is one series with several
files, each with its own text and table of contents. A narration has to be made from one file,
so the file is the unit, and rows of the same series share `kavita_series_id`.

| Column | Meaning |
|---|---|
| `kavita_chapter_id` | **unique**; Kavita's id of the chapter that owns the file. The sync matches rows on it |
| `kavita_series_id`, `kavita_library_id`, `kavita_volume_id` | where the file sits in Kavita |
| `series_title` | Kavita series name, trailing separators (` —`, `-`, `:` ...) stripped |
| `volume_label` | "Volume 1", a named volume or a chapter title; set only when the series has several files |
| `volume_number` | volume number from Kavita, else the 1-based position in the series |
| `title` | `series_title`, or `"<series> — <volume_label>"` for multi-file series |
| `sort_title` | Kavita `sortName` (library sort key) |
| `authors` | comma-separated; Kavita writers, else the OPF `dc:creator`s |
| `summary` | Kavita summary, else the OPF `dc:description` |
| `file_path` | path as Kavita reports it (inside Kavita's container) |
| `local_path` | path Lector opens: the mapped path, or a downloaded copy in `/data/cache/books` |
| `file_format` | `epub`, `pdf`, `archive`, `image` or `unknown` (Kavita `MangaFormat`, `FORMAT_NAMES`) |
| `word_count` | from Kavita |
| `is_gutenberg`, `gutenberg_id` | Project Gutenberg detection from the OPF (`gutenberg_id` is `""` once a non-Gutenberg book was checked, so the OPF is read only once) |
| `cover_cached` | a cover file exists in `/data/cache/covers` |
| `kavita_updated` | Kavita `lastChapterAdded` (informational) |
| `missing` | file gone from Kavita or unreadable; the row and its audio are kept |
| `created_at`, `last_synced` | |

### `renditions`

One generated audiobook: book x voice x speed. **Unique** on `(book_id, voice, speed)`
(`uq_rendition`); speeds are rounded to two decimals (`normalize_speed`) so the float comparison
is stable.

| Column | Meaning |
|---|---|
| `book_id` | FK `books.id`, `ON DELETE CASCADE` |
| `voice`, `speed` | TTS voice id and speed |
| `engine` | engine name when requested (`kokoro`, `openai`); informational, the worker always uses the current engine |
| `status` | `queued`, `running`, `paused`, `done`, `error`, `cancelled` (section 6) |
| `error` | last error text |
| `progress` | 0..1, done chapters / non-skipped chapters (chapter granularity) |
| `total_chapters`, `done_chapters`, `total_duration` | maintained by `Worker._update_totals` |
| `requested_by` | FK `users.id`, `ON DELETE SET NULL` |
| `created_at`, `updated_at`, `finished_at` | queue order is `created_at, id` |
| `m4b_path` | path of the built `audiobook.m4b`, if any |

### `chapters`

One row per section of the narration plan. **Unique** on `(rendition_id, index)`
(`uq_chapter`).

| Column | Meaning |
|---|---|
| `rendition_id` | FK, `ON DELETE CASCADE` |
| `index` | 0-based position; also the file name `NNN` |
| `title` | display title (`display_title`) |
| `status` | `pending`, `running`, `done`, `error`, `skipped` |
| `char_count` | narratable characters (paragraphs only) |
| `duration` | seconds, including the inserted pauses |
| `file_path` | the encoded file |
| `error` | why the chapter failed or was skipped |
| `text_hash` | SHA-256 of the spoken paragraphs (`Section.text_hash`), used on resume |

### `progress`, `bookmarks`, `settings`, `sync_log`

- `progress`: a user's position in a rendition. **Unique** on `(user_id, rendition_id)`
  (`uq_progress`). `chapter_index`, `position` (seconds in the chapter), `finished`,
  `playback_rate` (per-book override), `updated_at`, `started_at` (written, not exposed).
  Both FKs cascade.
- `bookmarks`: `user_id`, `rendition_id` (both cascade), `chapter_index`, `position`, `note`.
- `settings`: runtime settings, `key` primary key, `value` JSON-encoded (section 8).
- `sync_log`: `started_at`, `finished_at`, `status` (`running`, `ok`, `error`), `message`,
  `books_seen`. The admin page shows the newest row.

### Deletes and cascades

Cascades happen at two levels. In the ORM, `Book.renditions` and `Rendition.chapters` use
`cascade="all, delete-orphan"`. In SQLite, the FKs above carry `ON DELETE CASCADE` /
`SET NULL`, which take effect because `foreign_keys=ON` is set per connection; that is what
removes `progress` and `bookmarks` (they have no ORM relationship) when a rendition or user is
deleted. Books are never deleted by the application.

### Files on disk

All under `LECTOR_DATA_DIR` (`/data` in the container):

```
/data/lector.db (+ -wal, -shm)        database
/data/secret.key                      session-signing key (mode 0600), if LECTOR_SECRET_KEY is unset
/data/audio/<book_id>/<rendition_id>/ (LECTOR_AUDIO_DIR overrides /data/audio)
    000.mp3 000.json 001.mp3 ...      chapter audio (or .m4a) + read-along timeline
    .003.partial.wav                  float32 WAV of the chapter being generated (deleted after)
    003.part.mp3                      ffmpeg output before the atomic rename
    audiobook.m4b                     optional export (audiobook.part.m4b while building)
/data/cache/covers/<book_id>.jpg      cover bytes as Kavita sent them (may be PNG/WebP inside)
/data/cache/books/<kavita_chapter_id>.epub   downloaded copies when the file is not mounted
/data/cache/previews/<voice>.mp3      voice samples
```

`rendition_dir()` in `app/worker.py` builds the audio path. File names are the chapter index
zero-padded to three digits.

## 4. Kavita sync

Code: `app/kavita.py` (HTTP client and Kavita data shapes) and `app/sync.py` (database upsert).
Tested against Kavita 0.9.1.

### Authentication

`KavitaClient._login()` supports two flows:

- **API key** (preferred): `POST /api/Plugin/authenticate?apiKey=<key>&pluginName=Lector`
  returns a JWT. The key is the one Kavita shows under Settings -> 3rd Party Clients (the OPDS
  key).
- **Username/password**: `POST /api/Account/login` with `{username, password, apiKey: ""}`. The
  response also contains the user's API key, which the client keeps for image URLs.

The token is cached and refreshed proactively after 12 hours (`TOKEN_MAX_AGE`) under a lock.
`_request()` retries a `401` once with a fresh login. `_send()` retries timeouts, connection
errors and HTTP 502/503/504 twice with 0.5 s and 1.5 s back-off. Every failure surfaces as
`KavitaError` (or `KavitaAuthError`) with a short message; request URLs (which contain
`?apiKey=`) are never put in messages, and `app/main.py` lowers the `httpx`/`httpcore` loggers to
WARNING because they would log full URLs at INFO.

### Endpoints used

| Kavita endpoint | Used for |
|---|---|
| `GET /api/health` | reachability on the admin page (no auth) |
| `GET /api/Server/server-info-slim` | Kavita version on the admin page |
| `GET /api/Library/libraries` | libraries; filtered by `type` in `KAVITA_LIBRARY_TYPES` |
| `POST /api/Series/all-v2?pageNumber=&pageSize=100` | all series of a library (filter: field 19 Libraries, Equal, library id; sorted by sort name) |
| `GET /api/Series/volumes?seriesId=` | volumes -> chapters -> files of a series |
| `GET /api/Series/metadata?seriesId=` | writers and summary |
| `GET /api/Image/series-cover?seriesId=&apiKey=` | series cover |
| `GET /api/Image/chapter-cover?chapterId=&apiKey=` | per-volume cover for multi-file series |
| `GET /api/Download/chapter?chapterId=` | file download fallback (needs the Download role) |

`KavitaClient.series()` and `mark_series_read()` (`POST /api/Reader/mark-read`) exist but are
not called; Lector does not write to Kavita.

**Paging.** `series_in_library()` reads Kavita's `Pagination` response header (JSON with
`totalPages`, parsed by `parse_pagination`) and stops at the last page. Without the header (old
Kavita) it stops at the first short page. Series ids are de-duplicated in case the library
changes during the listing.

### From volumes to Book rows

`file_entries()` flattens a series' volumes into one entry per chapter that has a file, in
reading order: numbered volumes by number, then loose-leaf, then specials
(`_volume_sort_key`, `_chapter_sort_key`). A chapter with several files uses the first by path
(and logs it).

Kavita fills unknown numbers and titles with sentinels: `-100000` (loose leaf, "no volume") and
`100000` (specials), often as strings such as `"-100000"` in a chapter's `title` or `range`.
`is_sentinel()` and `_num()` filter them so they never become labels. Labels for multi-file
series come from `volume_label()`: a numeric volume name becomes `Volume N`, a named volume is
used verbatim, otherwise the chapter's `titleName`/`title`/`range` with the series-name prefix
stripped (`_strip_series_prefix`). Duplicate labels become `"<label>: <chapter title>"` or
`"<label>, part N"`.

`_sync_series()` then upserts each entry in its own short session. For each row it sets the
fields in section 3, resolves `local_path` (below), and fills `authors`/`summary`. When Kavita
returns no writers or summary, the sync reads the EPUB's OPF with `read_epub_metadata()`
(only `META-INF/container.xml` and the package document). That happens with Kavita libraries
whose metadata setting (`enableMetadata`) is off: Kavita then parses series from file names and
leaves writers and summary empty. (With metadata off Kavita may also fail to pick up many
Gutenberg EPUBs at all; that is on Kavita's side, so keep library metadata on.) Gutenberg
detection reads the OPF once per book.

### Path mapping and the download fallback

Kavita reports paths as seen inside its own container. `_local_path_for()`:

1. Maps the path with `settings.map_kavita_path()` (longest matching `KAVITA_PATH_MAP` prefix,
   on `/` boundaries). If that file is readable, use it.
2. Otherwise use `/data/cache/books/<chapter_id><ext>` if it exists and its size equals the
   `bytes` Kavita reports.
3. Otherwise download it via `GET /api/Download/chapter` (streamed to a `.part` file, 300 s
   timeout, atomic rename). This runs inside the sync, file by file.
4. If the download fails but an older copy exists, keep using the copy. Otherwise the book is
   `missing`, and `local_path` keeps the mapped path for diagnostics.

### Covers

A cover is fetched when the book has none cached, the file was deleted, or `force_covers` is
passed to `run_sync()` (no API caller passes it). Multi-file series try the chapter cover first,
then the series cover. Bytes are written atomically to `<book_id>.jpg` whatever the format; the
cover endpoint sniffs PNG/WebP/JPEG from the first bytes (`_image_media_type`).

### Missing and removed books

After all libraries are processed, every Book whose `kavita_chapter_id` was not seen is flagged
`missing = True`, except books in a library whose listing failed or a series whose sync raised:
their state is unknown, so they are left alone. A Kavita outage (login or `libraries()` failing)
aborts the sync with `status=error` and flags nothing. Books are never deleted, so audio and
progress survive a temporary removal; when the file reappears the next sync clears `missing`
(it is recomputed by `_local_path_for`). Note that removing a library type from
`KAVITA_LIBRARY_TYPES` flags that library's books missing.

### Idempotency, schedule, locking

Re-running a sync updates rows in place (matched on `kavita_chapter_id`), never duplicates
them, and only overwrites `authors`/`summary` with non-empty values. `run_sync()` takes a
module-level `threading.Lock` with `acquire(blocking=False)`: a second caller gets
`{"status": "busy"}` immediately. It writes a `sync_log` row at the start and finishes it with a
summary such as `4 book file(s) in 3 series across 1 library(ies); 1 new, 0 missing`, plus up to
five error messages. The `SyncScheduler` waits 5 s after boot, syncs, then sleeps
`SYNC_INTERVAL_MINUTES` measured from the end of the previous sync.

## 5. The EPUB text pipeline

Code: `app/epub_text.py`. Entry point `extract_book(path) -> BookText`, which holds metadata
and an ordered list of `Section`s (each with `title`, `paragraphs`, `headings`, `skipped`). The
worker turns each non-skipped section into one chapter file.

### 5.1 Package, spine and TOC

- `_read_opf()` finds the package document through `META-INF/container.xml`, falling back to
  the first `.opf` in the zip.
- `_opf_metadata()` reads title, authors (EPUB 2 `opf:role` or EPUB 3 `<meta refines>` roles;
  non-author creators are used only if there are no authors), language (default `en`),
  description (HTML stripped) and the Gutenberg id from `dc:identifier`/`dc:source`
  (`GUTENBERG_ID_RE`, e.g. `http://www.gutenberg.org/98`).
- The spine is the `itemref`s with `linear != "no"` that point at (X)HTML present in the zip.
  With no usable spine, all (X)HTML files are taken in name order.
- The TOC is the EPUB 3 nav document (`<nav epub:type="toc">`, else the first `<nav>`) or, if
  that yields nothing, the NCX (`navPoint`s). Both are flattened in document order to
  `(label, file, fragment)` triples. The nav document itself is not narrated.

### 5.2 Flattening XHTML into blocks

`_blocks_from_xhtml()` parses each spine file with lxml (lenient HTML mode) and walks `<body>`
with `_BlockCollector`. The result is a flat list of `Block(text, file_index, tag, ids,
link_chars, is_heading, pre)`.

- An element with no block-level children (`BLOCK_TAGS`) is a **leaf block**: its inline
  content is rendered to one string by `_inline_text()`. Source newlines become spaces
  (Gutenberg hard-wraps its HTML at about 72 columns); only `<br>` produces a line break.
  `<pre>` keeps its lines.
- An element with block children is split: runs of inline content between block children
  become their own blocks, and block children are walked recursively.
- `SKIP_TAGS` (`script`, `style`, `svg`, `img`, `math`, `sup`, ...) are dropped, so footnote
  reference numbers in `<sup>` never reach the text.
- **Pending ids.** Every `id` seen on the way down is queued in `pending_ids`; when a block
  with text is emitted it takes all queued ids. Anchors on empty elements (a common TOC target
  pattern, `<a id="chap01"/>` before the heading) therefore attach to the next block with
  text, which is what the TOC lookup needs.
- `link_chars` counts characters inside `<a>`, used later to detect link lists.

### 5.3 Project Gutenberg trimming

Gutenberg files wrap the book in a header and a long license. `extract_book()` scans the blocks:

- the first block matching `GUTENBERG_START_RE` is the start marker:
  `*** START OF THE PROJECT GUTENBERG EBOOK ... ***` (also `THIS`, any spacing between the
  asterisks), or the old-style header end `*END*THE SMALL PRINT! FOR PUBLIC DOMAIN ETEXTS...`;
- the first later block matching `GUTENBERG_END_RE` (`*** END OF THE PROJECT GUTENBERG ...`)
  is the end marker.

Everything up to and including the start marker and everything from the end marker on is
dropped (the marker blocks themselves are not narrated). With only a start marker the text runs
to the end of the book; with only an end marker it starts at the beginning. If the markers are
degenerate (start not before end) nothing is trimmed. Finding either marker sets
`is_gutenberg = True` on the `BookText` even if the OPF did not say so; trimming depends only on
the markers, not on the OPF flag. The map from spine file to first block (`file_first_block`) is
re-indexed onto the kept blocks; files that lie entirely in the dropped region disappear from it.

### 5.4 Chapterization

1. **TOC anchors -> block index.** Each TOC entry's `(file, fragment)` is looked up in an
   `(file_index, id) -> block index` table over the kept blocks. Without a fragment, or when the
   fragment is not found (for example it pointed into the trimmed header), the entry starts at
   the file's first kept block. Entries whose file has no kept text are dropped.
2. **Fallbacks.** With fewer than two starts from the TOC, `h1`-`h3` headings are used; with
   fewer than two headings, one chapter per spine file.
3. **Merge identical starts.** Starts are sorted; several TOC labels pointing at the same block
   are merged into one start with all labels. If the first start is not block 0, a "Front
   matter" start is inserted (or the book title, when there are no starts at all).
4. **Raw segments.** Each start runs to the next one. The section title is the last label at
   that start, else the first block's text if it is a heading, else `Section N`. All labels
   become the section's `headings`.
5. **Skip rules.** A segment is marked `skipped` ("table of contents / index") when its title
   matches `SKIP_TITLE_RE` (`Contents`, `Table of Contents`, `Illustrations`,
   `List of Illustrations`, `Index`, `Full Project Gutenberg License`), or when more than 60% of
   its characters are link text and it has more than three blocks (`LINK_RATIO_SKIP`). Skipped
   sections are kept in the plan, so chapter indexes stay stable, but never narrated.
6. **Carry tiny heading-only segments forward.** A non-skipped segment with fewer than 250
   characters (`MIN_CHAPTER_CHARS`; headings do not count) is not a chapter of its own. Its
   headings, plus its paragraphs that are not repeats of those headings, are prepended to the
   `headings` of the next non-skipped segment. Skipped segments in between are passed through
   and keep waiting. If the receiving section's title is a byline (`By ...`) or `Section N`, it
   takes the first carried heading as its title. Carrying can cascade (title page -> "Book the
   First" -> "Chapter I"). A tiny segment at the very end stays a section of its own.
7. **Split long sections.** Sections over 48,000 characters (`MAX_CHAPTER_CHARS`, about
   50 minutes of audio) are split at paragraph boundaries into `ceil(chars / 48000)` parts of
   about equal size, titled `"<title> (1/3)"`, `"(2/3)"`, ... Only part 1 keeps the headings.
8. **Titles.** For books with at most two narrated sections, a `Section N` or byline title is
   replaced by the book title. All titles go through `display_title()`: footnote markers
   removed, ALL-CAPS turned into title case by `smart_title()`, and a lone trailing period
   dropped (but not after `Mr.` or an initial).

### 5.5 What gets spoken

`Section.spoken_paragraphs()` is the narration of a section: each heading through
`heading_for_speech()`, then the paragraphs. If the first paragraph is the heading text itself
(compared with `_norm`, letters and digits only), it is dropped so the heading is not read
twice. `Section.text_hash()` is a SHA-256 over these spoken paragraphs; it changes when the
extraction (not the TTS cleanup) produces different text.

`heading_for_speech()`:

- strips footnote markers and collapses whitespace;
- `smart_title()`: ALL-CAPS to title case, keeping canonical roman numerals (`is_roman` accepts
  `XIV` but not `DID` or `CIVIL`), initials (`U.S.`), small words in lower case, `Wine-Shop`,
  `O'Brien`;
- after `Chapter`, `Book`, `Part`, `Volume`, `Canto`, `Letter`, `Section`, `Act`, `Scene` or
  `Stave`, an upper-case roman numeral becomes digits: `CHAPTER IV.` -> `Chapter 4.`;
- a heading that is only a roman numeral becomes `4.`;
- a final period is added so the TTS ends the heading with a falling tone.

### 5.6 Normalization for TTS

`clean_for_tts(text, language)` runs on every spoken paragraph just before chunking:

| Input | Output |
|---|---|
| NBSP, soft hyphen, zero-width space, BOM | removed / space |
| footnote markers `[12]`, `(*3)`, `{4}` | removed |
| `_italics_` (Gutenberg plain-text emphasis) | underscores removed |
| `…` | `...` |
| curly quotes, `«»`, `„` | straight quotes |
| `1840–1850` (dash between digits) | `1840 to 1850` |
| `word—word` | `word, word` |
| any other em/en dash or `―` | space |
| `*` (`* * *` scene breaks, `*emphasis*`) | space |
| `&c.` / `&` | `etc.` / `and` |
| `....` and longer | `...` |
| English text: `Æ`, `ø`, `ß`, `Đ`, `Þ`, ... | `AE`, `o`, `ss`, `D`, `Th` (`_ATOMIC`), then other accents stripped with NFKD (`Café` -> `Cafe`) |

Accents are kept for books whose OPF language is not English, since espeak-ng handles them.
Whitespace is collapsed, spaces before punctuation removed, and a comma left at the start of a
line by a removed dash is dropped. Line breaks (verse, `<br>`) are preserved.

Note the language split: transliteration follows the **book's** OPF language, while the
phonemizer language passed to the engine follows the **voice** (`describe_voice(voice)["lang"]`
in `Worker._process`).

### 5.7 Sentences and chunks

`split_sentences()` splits a line after `.`, `!`, `?` or `…` (plus closing quotes/brackets)
when the next sentence starts with a quote, bracket, capital or digit, but not after common
abbreviations (`Mr.`, `Dr.`, `St.`, `Vol.`, ...) or a single capital initial (`Edgar A. Poe`).

`chunk_for_tts(paragraphs, max_chars=380)` packs whole sentences greedily into chunks of at most
380 characters. A chunk never spans two paragraphs or two `\n`-separated lines. A sentence that
is longer than 380 characters on its own is cut by `_hard_split()`: at the last `, `, `; `,
`: `, ` - ` or `) ` in the second half of the window, else at the last space, else (a single
unbreakable token) hard at 380.

Why 380: one Kokoro inference takes at most 510 phonemes (`MAX_PHONEME_LENGTH` in kokoro-onnx);
longer input is split by kokoro-onnx itself at whatever punctuation it finds. English text
phonemizes to slightly more than one phoneme per character (the 377-character chunk in the
example below is 384 phonemes), and numbers or abbreviations expand further. 380 characters
leaves that headroom, so every chunk is one inference whose boundaries Lector chose (sentence or
clause ends), while still being long enough for natural prosody across a sentence or two. Smaller
chunks also make pause/cancel responsive (checks happen between chunks) and limit the damage of
a failed chunk to a few seconds of silence.

### 5.8 Worked example: *A Tale of Two Cities* (Gutenberg #98)

The Gutenberg EPUB has a cover wrapper, then `98-h-0.htm.xhtml` (header, title page, contents),
one file per chapter, and the license at the end. Flattened, the start of the block list is:

```
 #  file tag  ids                 text
 0  1    h2   pg-header ...       The Project Gutenberg eBook of A Tale of Two Cities
 1  1    div                      This eBook is for the use of anyone anywhere ...
 2-7 1   p                        Title: ... / Author: ... / Release date: ... / Credits: ...
 8  1    div  pg-start-separator  *** START OF THE PROJECT GUTENBERG EBOOK A TALE OF TWO CITIES ***
 9  1    h1   pgepubid00000       A TALE OF TWO CITIES
10  1    div                      A STORY OF THE FRENCH REVOLUTION
11  1    div                      By Charles Dickens
12  1    div                      Original           (link to a full-size illustration)
13  1    div                      Original
14  1    h2   pgepubid00001       Contents
15  1    td                       Book the First—Recalled to Life
...                               (one td per chapter)
3384 47  ...                      *** END OF THE PROJECT GUTENBERG EBOOK A TALE OF TWO CITIES ***
3385-3450                         license
```

1. Trimming keeps blocks 9 to 3383. The header (0-8) and the license (3384 on) are gone.
2. The TOC has `A TALE OF TWO CITIES` (`#pgepubid00000`, block 9), `Contents`
   (`#pgepubid00001`), `Book the First—Recalled to Life` (`#pgepubid00002` in file 2),
   `CHAPTER I. The Period` (`#pgepubid00003`, same file), `CHAPTER II. The Mail`, ...
3. Segments: the title page (five short blocks, under 250 characters) is carried. `Contents`
   matches `SKIP_TITLE_RE` and becomes skipped section 0. The `Book the First` segment holds only
   its heading, so it is carried too, collecting the title page. Both arrive in front of
   `CHAPTER I. The Period`.
4. Section 1 ends up with title `CHAPTER I. The Period` and headings
   `A TALE OF TWO CITIES`, `A STORY OF THE FRENCH REVOLUTION`, `By Charles Dickens`,
   `Original`, `Original`, `Book the First—Recalled to Life`, `CHAPTER I. The Period`. Its first
   paragraph, `CHAPTER I.` / `The Period` (an `<h2>` with a `<br>`), equals the last heading and
   is dropped from the spoken text.
5. Spoken, chapter 1 opens with: "A Tale of Two Cities. A Story of the French Revolution. By
   Charles Dickens. Original. Original. Book the First—Recalled to Life. Chapter 1. The Period.
   It was the best of times, ...". (The two "Original." lines are illustration link captions
   that the pipeline does not recognize; they cost about a second.)
6. The first paragraph after cleaning is 611 characters, one sentence. It is split at the last
   `, ` in the window: a 377-character chunk ending "...going direct to Heaven," and a
   234-character chunk starting "we were all going direct the other way, in short, ...". The
   em dash in "the other way—in short" became ", " (`word—word` rule).
7. `CHAPTER II. The Mail` gets the plain heading "Chapter 2. The Mail.", and
   `CHAPTER I. Five Years Later` (section 7) starts with the carried
   "Book the Second—the Golden Thread." heading.

Result: 46 sections, one skipped (Contents), 747,875 narrated characters. Print this plan for
any book with `python -m app.epub_text <file.epub>` (add `--dump` for the cleaned text).

## 6. The generation worker

Code: `app/worker.py`, class `Worker`, singleton `worker`.

### 6.1 Rendition lifecycle

```
          POST /books/{id}/renditions          POST .../pause             POST .../resume
 (new) ----------------------------> queued -----------------> paused ------------------> queued
                                      |  ^  \                    |
                         _next_job()  |  |   \ POST .../cancel   | POST .../cancel
                                      v  |    v                  v
                                   running ---------------> cancelled --POST .../retry or
                                   /  |  \   (cancel/pause seen      \  re-request--> queued
                     all chapters /   |   \  by _check_control)
                     done or     v    |    v  any chapter error, or
                     skipped  done    |   error ---POST .../retry---> queued
                                      |
                       process stop / crash: stays "running" in the DB,
                       requeue_interrupted() -> queued on next start
```

- `_next_job()` claims the oldest `queued` rendition (`created_at`, `id`) by setting it to
  `running` and clearing `error`. The loop sleeps on a wake event for up to 15 s when the queue
  is empty; API endpoints that queue work call `worker.wake()`.
- Transitions from the API are defined in `_TRANSITIONS` in `app/routers/library.py`:
  pause from `queued`/`running`, resume from `paused`, cancel from `queued`/`running`/`paused`,
  retry from `error`/`cancelled`. Anything else is `409`. Retry also resets `error` and
  `running` chapters to `pending`. Requesting an existing voice+speed returns the existing
  rendition and re-queues it only if it was `cancelled`.
- `requeue_interrupted()` runs at worker start: `running` renditions become `queued`,
  `running` chapters become `pending`.
- Deleting a rendition (`DELETE /api/renditions/{rid}`, admin) first sets a running one to
  `cancelled` so the worker stops at its next check, then removes the files and the row.

The API only writes `status`; the worker notices. `_check_control(rid)` raises `CancelledError`
when the process is stopping or the row is `paused`, `cancelled` or gone. It is called before
each chapter and every 4 chunks (`CONTROL_EVERY`), so a pause takes effect within a few seconds
of synthesis. The worker never overwrites a `paused`/`cancelled` status (`run_job` checks before
recording an error, `_process` returns early if the status is no longer `running`).

### 6.2 Chapter states and resume

`_sync_chapters()` reconciles the chapter rows with a fresh `extract_book()` every time a job
starts or resumes:

- first run: one row per section, `skipped` for skipped sections, else `pending`, with
  `char_count` and `text_hash`;
- **section count changed** (the EPUB, or the pipeline, now produces a different plan): all
  chapter rows and all `NNN.*` files are deleted and the plan is rebuilt from scratch;
- **same count, different `text_hash`**: that chapter is reset to `pending` (duration, file and
  error cleared) and narrated again; its title and `char_count` are refreshed;
- otherwise rows are kept: `done` and `skipped` chapters are not touched again.

Per chapter: `pending -> running -> done | error | skipped`. A paused/cancelled chapter goes
back to `pending` and is redone from its start on resume (there is no mid-chapter resume; the
partial WAV is deleted). `skipped` with error "no narratable text" means the section had nothing
left after `clean_for_tts` (`NothingToNarrate`). A chapter in `error` does not stop the job: the
worker moves on, and at the end the rendition becomes `error` with a message listing up to three
failed chapters. A rendition where every section was skipped is also `error`. Otherwise it is
`done` with `progress = 1.0`. `finished_at` is set in both cases.

`_update_totals()` recomputes `done_chapters`, `progress` (done / non-skipped) and
`total_duration` after every chapter.

### 6.3 Narrating one chapter

`_narrate_section()`:

1. Builds `items`: each spoken paragraph, cleaned with `clean_for_tts`, chunked with
   `chunk_for_tts`, flagged as heading if its position is within `len(section.headings)`.
2. Opens `.NNN.partial.wav` in the rendition folder as a mono 32-bit float WAV at the engine's
   sample rate (24 kHz) with `soundfile`, and streams every chunk into it. Memory use does not
   grow with chapter length. Disk use for this file is about 346 MB per hour of audio (96 KB/s),
   so roughly 290 MB for a maximum-size chapter.
3. Inserts silence: 0.22 s between chunks of a paragraph (`CHUNK_GAP`), 0.55 s after a paragraph
   (`PARAGRAPH_GAP`), 1.0 s after a heading (`HEADING_GAP`). The pause after the last paragraph
   is part of the file.
4. Records a timeline entry per paragraph: `{"start", "end", "text", "heading"}` in seconds,
   where `end` is before the trailing pause and `text` is the cleaned text. Example:

   ```json
   [{"start": 0.0, "end": 0.939, "text": "Chapter One.", "heading": true},
    {"start": 1.939, "end": 6.376, "text": "The morning was grey and ...", "heading": false}]
   ```

5. Tracks the peak sample and encodes with `encode_wav()`: `ffmpeg -i wav -ac 1
   [-af volume=G] -c:a libmp3lame -b:a <AUDIO_BITRATE> -id3v2_version 3` (or `-c:a aac
   -movflags +faststart` for m4a), with `title`, `album` (book title), `artist` (authors),
   `track` and `genre=Audiobook` tags, into `NNN.part.<ext>`, then renamed to `NNN.<ext>`. The
   gain `G = min(1, 0.89 / peak)` is a peak-based attenuation so no chapter clips and loud
   chapters are brought down to the same peak (`TARGET_PEAK`); it is not EBU R128 loudness
   normalization, and quiet chapters are never amplified.
6. Writes `NNN.json` atomically, then marks the chapter `done` with its duration (samples
   written / sample rate). The timeline is written first so a `done` chapter always has one.

### 6.4 Synthesis errors

`_synth()` wraps each engine call:

- on an exception, retry once with the text reduced to ASCII (some characters break the
  phonemizer). If nothing is left after the reduction, insert 0.3 s of silence and count it as
  success;
- if the retry also fails, insert 0.3 s of silence and count a failure;
- empty audio becomes 0.2 s of silence; NaN/inf samples become zeros.

Three failures in a row (`MAX_CONSECUTIVE_FAILURES`) raise and fail the chapter (the engine is
probably broken). A chapter where every chunk failed is also an error. Isolated failures only
log a warning. Before a job starts, `_process` also fails fast when the book file is missing,
is not an `.epub`, the engine is not ready, or the engine knows its voices and the voice is not
among them.

### 6.5 Live status

`Worker.status` is a `LiveStatus` dataclass, replaced with a fresh one (keeping the engine
fields) after each job. It holds the current rendition and book title, chapter index and title,
chunk counters, and the job's cumulative synthesized `audio_seconds` and synthesis
`wall_seconds` (encoding time is not included). `as_dict()` adds `realtime_factor` =
audio / wall once more than one second has been measured. `check_engine()` at thread start loads
and warms up the engine and records `engine_ready` / `engine_message`.

The admin status endpoint returns `as_dict()` directly. Rendition JSON blends it in through
`live_generation()` (section 8.6).

### 6.6 M4B export

`Worker.build_m4b(rid)` (under `_m4b_lock`) concatenates the rendition's `done` chapters whose
files exist, in index order:

- an `ffconcat` list of the chapter files;
- an `FFMETADATA1` file with `title`, `artist`, `album_artist`, `album`, `genre=Audiobook` and
  one `[CHAPTER]` per file (`TIMEBASE=1/1000`, `START`, `END`, `title`). Chapter boundaries use
  the `ffprobe` duration of each encoded file (encoders add padding), falling back to the stored
  duration; values are escaped by `_ffmeta_escape`;
- the cached cover as an attached picture, if there is one;
- `-c:a copy` when every chapter is already `.m4a`, otherwise re-encode to AAC at
  `AUDIO_BITRATE`; `-movflags +faststart -f mp4` into `audiobook.part.m4b`, renamed to
  `audiobook.m4b`.

The HTTP layer (`app/routers/media.py`) allows a build once at least one chapter is done, so a
partial M4B is possible. It reports `ready` without rebuilding when `audiobook.m4b` is newer
than every chapter file (`_m4b_up_to_date`), and `building` while the rid is in the in-memory
`_m4b_building` set.

### 6.7 Performance and size

| Measure | Value |
|---|---|
| Kokoro on an Apple M4, `TTS_THREADS=8` | about 5x realtime (one hour of audio in about 12 minutes) |
| Speech rate (af_heart, 1.0x) | about 16 characters of text per second of audio (`CHARS_PER_AUDIO_SECOND`); the CLI estimate in `epub_text.main` uses 15 |
| A 750,000-character novel (*A Tale of Two Cities*) | about 13 hours of audio, about 2.6 hours to generate at 5x |
| 64 kbps mono MP3 | 28.8 MB per hour (measured about 29 MB) |

Generation time scales with the cores onnxruntime can use. The model is loaded once per process
(about 350 MB of model files) and warmed up with one short sentence.

## 7. TTS engines

Code: `app/tts/`. `get_engine()` in `app/tts/__init__.py` creates a process-wide singleton from
`TTS_ENGINE` on first use: `openai` selects `OpenAICompatibleEngine`, anything else
`KokoroEngine`.

### 7.1 Interface

`TTSEngine` (`app/tts/base.py`):

| Member | Contract |
|---|---|
| `name: str` | stored on new renditions |
| `sample_rate: int` | rate of the returned samples (24000) |
| `voices() -> list[dict]` | `[{id, name, language, gender}]` |
| `synthesize(text, voice, speed, lang) -> np.ndarray` | float32 mono samples at `sample_rate`; raise on failure |
| `ready() -> (bool, str)` | can the engine work now (loads models / checks the server) |
| `warmup()` | optional, called once at worker start |
| `has_voice(voice) -> bool or None` | `None` means "cannot tell" and skips the worker's voice check |

Voice ids follow the Kokoro scheme `<language letter><gender letter>_<name>`, e.g. `af_heart`
(American English, female, Heart) or `bm_george`. `describe_voice()` derives display data and
the espeak-ng language code from the id via `VOICE_META`:

| Letter | Language | espeak-ng code |
|---|---|---|
| a | American English | `en-us` |
| b | British English | `en-gb` |
| e | Spanish | `es` |
| f | French | `fr-fr` |
| h | Hindi | `hi` |
| i | Italian | `it` |
| j | Japanese | `ja` |
| p | Brazilian Portuguese | `pt-br` |
| z | Mandarin Chinese | `cmn` |

Ids that do not match the pattern are treated as American English, female. The second letter
`f` means female, anything else male. `describe_voice` is used for every engine (voice names in
rendition JSON, the phonemizer language), so voices from another engine are labeled with the
same rules.

### 7.2 `KokoroEngine`

- Uses `kokoro_onnx.Kokoro` with `kokoro-v1.0.onnx` (about 325 MB) and `voices-v1.0.bin`
  (about 28 MB). Missing files are downloaded on first load from
  `https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/` (to a `.part`
  file, then renamed) unless `KOKORO_AUTO_DOWNLOAD` is off.
- `TTS_THREADS > 0` builds an `onnxruntime.InferenceSession` with that many intra-op threads,
  one inter-op thread and the CPU provider, passed to `Kokoro.from_session`. If that fails it
  logs a warning and uses onnxruntime defaults.
- Loading is lazy and guarded by `_lock`; `ready()` triggers it and turns exceptions into
  `(False, message)`.
- `synthesize()` rejects unknown voices, replaces an unsupported language code with the voice's
  own, clamps speed to 0.5-2.0 (kokoro-onnx asserts this range), and holds `_synth_lock` because
  espeak-ng/phonemizer is not guaranteed thread-safe.
- `voices()` lists the ids in the voices file (54 in v1.0). If the model cannot be loaded it
  returns a fixed list of 11 English voices so the UI still renders.
- `warmup()` synthesizes "Hello." once.

### 7.3 `OpenAICompatibleEngine`

For an external server that speaks OpenAI's speech API (Kokoro-FastAPI, openedai-speech):

| Call | Purpose |
|---|---|
| `GET {TTS_OPENAI_URL}/models` | `ready()`: any status below 500 counts as ready |
| `GET {TTS_OPENAI_URL}/audio/voices` | `voices()` (Kokoro-FastAPI extension; `{"voices": [...]}` or a list). Falls back to five Kokoro ids |
| `POST {TTS_OPENAI_URL}/audio/speech` | `{"model", "input", "voice", "speed", "response_format": "wav"}` with `Authorization: Bearer {TTS_OPENAI_KEY}` |

The WAV is decoded with `soundfile`, multi-channel audio is averaged to mono, and any rate other
than 24 kHz is converted by linear interpolation (`np.interp`), so all chapters of a rendition
share one rate. The `lang` argument is ignored (the server decides), speed is passed through
unclamped (the API already limits it to 0.5-2.0), `has_voice()` is `None`, and the HTTP timeout
is 300 s per chunk.

### 7.4 Adding an engine

Subclass `TTSEngine`, implement `voices()` and `synthesize()` (and `ready()`, `has_voice()`
when you can), add a branch to `get_engine()` and its settings to `app/config.py`. See
DEVELOPMENT.md for the checklist.

## 8. HTTP layer

### 8.1 Router map

| Module | Prefix | Endpoints |
|---|---|---|
| `app/routers/auth.py` | `/api` | `GET/POST /setup`, `POST /auth/login`, `/auth/logout`, `/auth/register`, `GET/PUT /auth/me`, `PUT /auth/password` |
| `app/routers/library.py` | `/api` | `GET /library`, `GET /books/{id}`, `GET /books/{id}/cover`, `GET /voices`, `GET /voices/{voice}/preview`, `POST /books/{id}/renditions`, `GET /renditions/{rid}`, `POST /renditions/{rid}/pause|resume|cancel|retry`, `DELETE /renditions/{rid}` |
| `app/routers/media.py` | `/api` | `GET/HEAD /renditions/{rid}/chapters/{i}/audio`, `GET .../timeline`, `POST/GET /renditions/{rid}/m4b` |
| `app/routers/progress.py` | `/api` | `GET/PUT /progress/{rid}`, `POST /progress/{rid}/beacon`, `GET/POST /renditions/{rid}/bookmarks`, `PUT/DELETE /bookmarks/{id}` |
| `app/routers/admin.py` | `/api/admin` (router-level `admin_user` dependency) | `GET /status`, `POST /sync`, `GET/POST /users`, `PUT/DELETE /users/{id}`, `GET/PUT /settings` |
| `app/main.py` | | `GET /api/health`, catch-all `/api/{rest}` -> JSON 404, `/static` mount, `/manifest.webmanifest`, `/sw.js` (with `Service-Worker-Allowed: /`), `/{path}` -> `index.html`. OpenAPI UI at `/api/docs`, schema at `/api/openapi.json` |

Request bodies are Pydantic models in `app/schemas.py` (`extra="ignore"`). Responses are plain
dicts built by the serializers in `app/routers/_helpers.py` (`user_dict`, `rendition_dict`,
`chapter_dict`, `progress_dict`, `bookmark_dict`, `book_summary`, `book_detail`,
`rendition_full`).

### 8.2 Authentication

- Passwords: bcrypt with 12 rounds (`hash_password`). Passwords are 6 characters to 72 bytes
  (bcrypt's input limit, `_check_password`); usernames match `^[A-Za-z0-9_.-]{2,64}$`; login
  compares usernames case-insensitively.
- Tokens: `itsdangerous.URLSafeTimedSerializer` with the secret key and salt `lector-session`,
  payload `{"uid": <id>}`, valid for `SESSION_DAYS` (checked at read time with the current
  setting). Login, setup and register return the token in the body and set it as the cookie
  `lector_session` (HttpOnly, SameSite=Lax, `Secure` when `COOKIE_SECURE`, path `/`).
- `_token_from_request()` looks, in order, at `Authorization: Bearer <token>`, the cookie, and a
  `?token=` query parameter (for `<audio>`/`<img>` URLs that cannot send headers; the bundled
  web client relies on the cookie instead).
- Sessions are **stateless**: nothing is stored server-side. Logout only deletes the cookie in
  that browser. Changing a password does not invalidate tokens already issued to other devices;
  they stay valid until they expire. Deleting a user invalidates their tokens (the user lookup
  fails), and replacing the secret key invalidates everyone's.
- Dependencies: `optional_user`, `current_user` (401 "Not signed in"), `admin_user`
  (403 "Admin only"). The user row is loaded on every request, so role changes apply at once.

### 8.3 Roles

| Who | Can |
|---|---|
| any signed-in user | browse the whole library, play any rendition, keep their own progress and bookmarks, download M4Bs, voice previews, edit their own preferences and password |
| requester (admin, or anyone while `allow_user_requests` is on; `require_requester`) | request narrations, build M4Bs |
| admin | pause/resume/cancel/retry/delete renditions, sync, users, runtime settings, status |

Bookmarks are scoped to their owner (`_own_bookmark` returns 404 for someone else's). The
library itself is shared; there is no per-user library visibility. The last admin cannot be
demoted, and an admin cannot delete themselves.

### 8.4 Runtime settings

`default_voice`, `default_speed`, `allow_user_requests` and `allow_registration` are stored
JSON-encoded in the `settings` table and read on every request by `effective_settings()`, which
falls back to the environment value when no row exists; once saved in the UI, a key ignores its
environment variable. Details in CONFIGURATION.md.

### 8.5 Audio and Range requests

`chapter_audio` serves a chapter only when its status is `done` and the file exists (else 404).
`ranged_file_response()` implements byte ranges by hand:

- `parse_range()` honors the first range of a `bytes=` header: `start-end`, `start-` and
  suffix `-N`. A malformed header is ignored and the whole file is served (RFC 9110 allows
  this); an unsatisfiable one is `416` with `Content-Range: bytes */<size>`.
- Responses carry `Accept-Ranges: bytes`, `Content-Length`, `Last-Modified` and
  `Cache-Control: private, max-age=3600`; partial ones are `206` with `Content-Range`.
- The body is streamed in 64 KiB pieces (`iter_file`). `HEAD` returns the headers only.
- Media types come from the file suffix (`.mp3` audio/mpeg, `.m4a`/`.m4b` audio/mp4).

Browsers need this to seek in long MP3s without downloading them. Covers (`private,
max-age=86400`), voice previews (`max-age=604800`) and M4B downloads use `FileResponse`.

### 8.6 `progress` vs `generation_progress`

Two different "progress" values exist and share a name in places:

- In `rendition_dict()`: `progress` is the rendition row's 0..1 generation fraction
  (chapter-granular), `generation_progress` is the finer live value from `live_generation()`,
  and `live` is `null` or the worker's current chapter, chunk counters, `chapter_fraction`,
  `realtime_factor` and `eta_seconds`.
- Wherever a rendition is returned together with the user's listening position (each rendition
  in `GET /api/books/{id}` via `rendition_full()`, and `GET /api/renditions/{rid}`), `progress`
  is replaced by the user's Progress object (or `null`), and the generation fraction is only in
  `generation_progress`. In `GET /api/renditions/{rid}` that field is set from the
  chapter-granular value (`d["generation_progress"] = d["progress"]` in `rendition()`), not the
  live blend; `live` is still present.
- `BookSummary.progress` and `ContinueItem.progress` are always the user's Progress.

`live_generation()` exists because `Rendition.progress` only moves when a chapter finishes; a
one-chapter book would sit at 0% for hours. While the worker is on this rendition it returns
`(done chapters + chunk / chunks) / narrated chapters`, plus an ETA of
`remaining characters / 16 / max(realtime factor or 4, 0.5)` seconds.

Listening `percent` and `remaining_seconds` (`progress_position`) are computed over the
durations of `done` chapters only, so they change as more chapters are generated.

### 8.7 Errors and the SPA fallback

- `HTTPException` produces `{"detail": "<message>"}` with the status code.
- Validation errors are `422` with a flattened string, e.g.
  `{"detail": "speed: Input should be less than or equal to 2"}` (`_format_validation_error`).
- Unknown `/api/...` paths are a JSON 404 (the catch-all route in `app/main.py`), so API typos
  never return the HTML shell.
- Any other path returns `app/static/index.html` with `Cache-Control: no-cache`; the client
  router handles it.
- Unhandled exceptions are Starlette's plain-text `500 Internal Server Error`.

## 9. Front end

Files in `app/static/`, served as-is (no build step, no external assets):

| File | Content |
|---|---|
| `index.html` | shell: top bar, `<main id="view">`, mini-player, toasts, `<dialog>`, the single `<audio id="audio">`, inline pre-paint theme script |
| `app.js` | the whole client, one ES module (about 2,000 lines) |
| `app.css` | styles, dark/light variables |
| `sw.js` | service worker for the static shell |
| `manifest.webmanifest`, `icons/icon.svg` | PWA metadata |

### 9.1 Structure of `app.js`

In file order:

- **Helpers.** `$`/`$$`, the `html` tagged template (escapes every interpolated value; `raw()`
  marks trusted HTML), formatters, `store` (`localStorage` under `lector.*`, wrapped in
  try/catch).
- **Icons, toasts, dialogs** (`toast`, `dialog`, `confirmDialog`, `promptDialog`).
- **`api(path, {method, body, quiet, no401})`.** Every request goes through it: same-origin
  cookie, JSON in and out, `detail` shown as a toast unless `quiet`, and a `401` clears the user
  and redirects to `#/login` (remembering `returnTo`) unless `no401`.
- **State and theme.** `state` (setup info, user, voices cache, `returnTo`, route) and
  `applyTheme()`.
- **Router.** Hash routes `#/`, `#/book/<id>`, `#/player/<rid>` (optional `?ch=N`),
  `#/settings`, `#/admin`, `#/login`, `#/setup`. `route()` enforces setup and login redirects
  and admin-only access, runs the previous view's cleanups, and passes the view a `ctx` with
  `alive()` (false once the user navigated away), `onCleanup()` and `every(ms, fn)` (an interval
  cleared on navigation).
- **Player singleton `P`.** One `<audio>` element for the whole session, so playback continues
  while browsing. `P` holds the rendition, chapter index, Progress, bookmarks, timeline, rate,
  pending seek, `waiting` (current chapter not generated yet), `autoResume`, sleep timer and a
  listener set. Functions: `playerOpen`, `playerUnload`, `setChapter`, `play`, `seek`, `skip`,
  `prevChapter`/`nextChapter` (skip `skipped`/`error` chapters), `setRate`, `saveProgress`,
  `beacon`, `onEnded` (advance, or mark finished), read-along (`loadTimeline`, `updatePara`),
  bookmarks, sleep timer (minutes or end of chapter), MediaSession and keyboard shortcuts
  (space/k, arrows, shift+arrows, `[` `]`, b, m).
- **Sheets** for chapters, speed and sleep; **mini-player** (`renderMini`) shown on every page
  except the player while something is loaded.
- **Views.** `viewLogin`, `viewSetup`, `viewLibrary`, `viewBook`, `viewPlayer`,
  `viewSettings`, `viewAdmin`.
- **`boot()`.** Applies the cached theme, sets up MediaSession, calls `GET /api/setup` and
  `GET /api/auth/me`, starts the router, registers the service worker.

### 9.2 Polling

| Where | Cadence | Condition |
|---|---|---|
| Library | 15 s | only while some rendition is `queued` or `running` and the tab is visible |
| Book page | 5 s tick | refresh every tick while a rendition is `running`; every third tick (15 s) while only queued/paused ones exist; every tick while an M4B build is pending |
| Player | 15 s | while chapters are `pending`/`running` and the rendition is active, or while waiting for the current chapter; playback starts automatically when it becomes ready |
| Admin | 5 s | always (status, queue, users); plus one refresh 1.5 s after "Sync now" |

### 9.3 Saving progress

`saveProgress()` sends `PUT /api/progress/{rid}` with `{chapter_index, position, playback_rate,
finished}`:

- every 10 s while playing (checked on `timeupdate`);
- on pause (not at the natural end), after a seek (debounced 800 ms), on chapter change or a
  seek to 0 in `setChapter`, on a rate change, when the player unloads (another book, logout);
- with `finished: true` when the last chapter ends;
- on `visibilitychange` to hidden and on `pagehide`, `beacon()` uses
  `navigator.sendBeacon('/api/progress/{rid}/beacon', blob)` (POST only, possibly `text/plain`,
  which is why the server parses that body itself), falling back to `fetch(..., {keepalive:
  true})`.

The rate is also saved to the user (`PUT /api/auth/me`, debounced 700 ms); the Progress
`playback_rate` acts as a per-book override when a book is reopened.

### 9.4 MediaSession, service worker, theme

- MediaSession: metadata (chapter title, authors, book title, cover or app icon), position state
  at most once a second, and handlers for play, pause, stop, seek backward/forward, previous and
  next track (chapters), and seek-to. This gives lock-screen and headset controls on phones.
- `sw.js` caches the static shell only (`/`, `app.css`, `app.js`, the icon, the manifest) in
  `lector-shell-v1`, network-first with the cache as offline fallback. It never touches `/api`
  (data, covers, audio) or Range requests. It is registered only on HTTPS, `localhost` or
  `127.0.0.1`, because browsers allow service workers only in secure contexts; on plain
  `http://<lan-ip>` the app works without it.
- Theme: `User.theme` (`dark`, `light`, `auto`) is stored server-side and mirrored to
  `localStorage['lector.theme']`, which the inline script in `index.html` applies before first
  paint. `dark`/`light` set `data-theme` on `<html>`; `auto` removes the attribute and the CSS
  follows `prefers-color-scheme`.

## 10. Security model and known limitations

Lector is built for a trusted home network. Points to be aware of:

- **No rate limiting** on login or anything else. bcrypt slows guessing but does not stop it.
  Do not expose Lector to the internet without a reverse proxy that adds TLS and, ideally, its
  own authentication or rate limits.
- **Stateless sessions.** Tokens cannot be revoked individually. A password change does not log
  out other devices; a stolen token works until `SESSION_DAYS` runs out. To log everyone out,
  replace the secret key (delete `/data/secret.key` or change `LECTOR_SECRET_KEY`) and restart.
- **Cookies.** HttpOnly and SameSite=Lax, which also covers CSRF for the state-changing
  endpoints (cross-site POST/PUT/DELETE requests do not carry a Lax cookie). "Same-site"
  ignores the port, so other web apps served from the same host name count as same-site and
  are not covered. `Secure` is off by
  default because the default deployment is plain HTTP; set `COOKIE_SECURE=true` behind HTTPS.
- **`?token=` URLs.** Accepted for media, but a token in a URL can end up in proxy logs, uvicorn
  access logs and browser history. The bundled client does not use it.
- **Shared library.** Every signed-in user sees every book and every rendition; only progress,
  bookmarks and preferences are private. Any signed-in user can trigger voice preview generation
  (CPU time). Open registration is off by default.
- **Kavita credentials.** The API key or password lives in the container environment (`.env`).
  The API key is sent to Kavita as a query parameter (`/api/Plugin/authenticate`, image URLs),
  so it may appear in Kavita's own logs; Lector keeps it out of its logs by silencing `httpx`
  request logging and never including URLs with parameters in error messages. Use a dedicated,
  low-privilege Kavita user that can see only the libraries to narrate. Lector needs read
  access only; it never writes to Kavita.
- **Download role.** Needed only for the fallback when the books folder is not mounted into
  Lector (`/api/Download/chapter`). With a correct `BOOKS_PATH`/`KAVITA_PATH_MAP` it can be
  withheld.
- **File access.** The books are mounted read-only. Lector opens only paths Kavita reports,
  mapped by `KAVITA_PATH_MAP`, and writes only under `/data` and the models folder.
- **EPUBs are trusted input.** Files are read from the zip in memory by name (nothing is
  extracted to disk) and parsed with lxml through BeautifulSoup, with no special defences
  against malformed or hostile archives (for example zip bombs). The library is assumed to be
  your own.
- **Quirk.** Deleting a rendition while it is being generated logs a traceback from the worker
  (the chapter row is gone when the worker tries to reset it to `pending`); the deletion itself
  completes.

## 11. Design decisions and trade-offs

**SQLite and a thread instead of Celery/Redis/Postgres.** TTS on a CPU saturates the machine,
so there is never a reason to run two jobs at once; a single consumer thread is the correct
concurrency, and a `status` column is a sufficient queue. SQLite in WAL mode handles the
handful of writes per chunk and the polling reads easily, needs no service, and makes backups a
file copy. The price is the hard `--workers 1` rule and no horizontal scaling, which a home
server does not need. Long work never holds a DB session, so request handlers do not wait on the
worker.

**One audio file per chapter, not one big file.** Chapters become playable as soon as they are
done; a failure or a text change costs one chapter, not the book; resume after a restart is
"skip the done files"; seeking and Range requests stay cheap; chapter navigation needs no
container-level chapter support in the browser. Players that want one file get the M4B export,
built from the same chapter files.

**Pre-generate instead of live TTS.** CPU Kokoro at about 5x realtime would technically keep up
with one listener at 1x, but not with 2x playback, several listeners, seeking ahead, or slower
hardware, and it would couple playback quality to server load. Pre-generated files can be
cached, downloaded, exported and served by any HTTP client. The cost is disk space (about 29 MB
per hour) and waiting for the first chapter.

**Vanilla JS, no build step.** The client is one module, one stylesheet and an HTML shell. There
is no toolchain to install or keep updated, no CDN (it must work on a LAN without internet), and
the files in the repo are exactly what the browser runs. The `html` tagged template provides
escaping, and the hash router keeps the server's SPA fallback trivial. The trade-off is a large
single file and hand-written DOM updates.

**A Book per Kavita file.** Kavita's series can hold several files with independent text and
TOCs, and Kavita's chapter id is the stable identifier for a file. Narration needs a single
text, so one row per file is the only unit that maps cleanly; series grouping is kept through
`kavita_series_id`, `series_title` and `volume_label`.

**Flag missing books instead of deleting them.** Audio takes hours to generate, and a
mis-mounted volume or a Kavita rescan should not destroy it or the listeners' progress.

**Trim Gutenberg by markers, not by OPF flag.** The markers are reliable across Gutenberg's file
generations; the OPF identifier is not always present. Detection by marker also covers
Gutenberg texts repackaged by others.
