# Troubleshooting

Each entry lists the **symptom** (what you see), the likely **cause**, and the **fix**. Start with
the diagnostic tools below; most problems show up clearly in the logs or on the admin page.

## Contents

- [Diagnostic tools](#diagnostic-tools)
- Kavita and the library
  - [Kavita unreachable](#kavita-unreachable)
  - [Kavita rejects the API key (401)](#kavita-rejects-the-api-key-401)
  - [Sync finds 0 books](#sync-finds-0-books)
  - [Books show "Missing"](#books-show-missing)
  - [Cover not showing](#cover-not-showing)
- Generating audio
  - ["Book file not found" error on an audiobook](#book-file-not-found-error-on-an-audiobook)
  - ["Only EPUB books can be narrated right now"](#only-epub-books-can-be-narrated-right-now)
  - [Rendition stuck in queued, or "TTS engine not ready"](#rendition-stuck-in-queued-or-tts-engine-not-ready)
  - [Pages spin for minutes on the first start](#pages-spin-for-minutes-on-the-first-start)
  - [Very slow generation](#very-slow-generation)
  - [Disk full during generation](#disk-full-during-generation)
  - [Permission denied on /data](#permission-denied-on-data)
  - [M4B build fails](#m4b-build-fails)
  - [Phonemizer or synthesis warnings in the logs](#phonemizer-or-synthesis-warnings-in-the-logs)
- Narration quality
  - [Wrong pronunciation, wrong language, or accents read oddly](#wrong-pronunciation-wrong-language-or-accents-read-oddly)
  - [Chapters split oddly, or the Contents page is narrated](#chapters-split-oddly-or-the-contents-page-is-narrated)
- Listening and accounts
  - [Audio plays but seeking fails](#audio-plays-but-seeking-fails)
  - ["Could not load this chapter's audio."](#could-not-load-this-chapters-audio)
  - [Progress not saving](#progress-not-saving)
  - [Signed out after changing LECTOR_SECRET_KEY](#signed-out-after-changing-lector_secret_key)
  - [Sign-in succeeds but bounces back to the sign-in page](#sign-in-succeeds-but-bounces-back-to-the-sign-in-page)
  - ["Passwords do not match" and other form messages](#passwords-do-not-match-and-other-form-messages)
  - [Forgotten admin password](#forgotten-admin-password)
  - [Settings in .env have no effect](#settings-in-env-have-no-effect)
  - [Old interface after an upgrade](#old-interface-after-an-upgrade)
- [Reporting a bug](#reporting-a-bug)

## Diagnostic tools

**Logs.** Lector's own messages look like
`2026-10-24 16:42:08,485 INFO lector: Lector 0.1.0 started (data dir /data)`: time, level, the
part of Lector (`lector.sync`, `lector.worker`, `lector.tts`, `lector.kavita`, `lector.api`) and
the message. Lines starting `INFO:     <ip> - "GET /api/...` are the web server's access log.

```sh
docker compose logs -f lector                                    # everything, live
docker compose logs lector | grep -E ' (WARNING|ERROR) '         # only problems
docker compose logs -f lector | grep -vE '"(GET|HEAD|POST|PUT|DELETE) /'   # hide the access log
```

For more detail set `LOG_LEVEL=DEBUG` in `.env` and run `docker compose up -d`.

**Admin page.** The Kavita, TTS engine, Worker and Storage cards, the last sync message, and the
queue (with each failed audiobook's error) answer most questions. See the
[user guide](USER-GUIDE.md#the-admin-page).

**Inside the container.** The image has Python but no `sqlite3` command, so database queries use
Python. These examples only read:

```sh
# Libraries the Kavita user can see: id, type (2 = Book, 4 = Light Novel), name
docker exec lector python -c "from app.sync import kavita; [print(l['id'], l['type'], l['name']) for l in kavita().libraries()]"

# Books Lector considers missing
docker exec lector python -c "import sqlite3; db=sqlite3.connect('/data/lector.db'); [print(r) for r in db.execute('select id, title, file_path, local_path from books where missing=1')]"

# Audiobooks and their state
docker exec lector python -c "import sqlite3; db=sqlite3.connect('/data/lector.db'); [print(r) for r in db.execute('select r.id, b.title, r.voice, r.status, r.done_chapters, r.total_chapters, r.error from renditions r join books b on b.id=r.book_id')]"

# The chapter plan Lector builds for an EPUB (SKIP marks skipped sections)
docker exec lector python -m app.epub_text "/books/Author/Title.epub"
```

On the host you can also open a *copy* of the database with the `sqlite3` tool
(`sqlite3 /path/to/lector-data/lector.db`), but avoid writing to it while Lector runs.

## Kavita unreachable

**Symptom.** The admin page's Kavita card says **Unreachable**. The last sync shows status
`error` with a message like `cannot connect to Kavita at http://kavita:5000: ...`. The log has
`WARNING lector.api: kavita status check failed: ...` or `ERROR lector.sync: sync failed`.

**Cause.** `KAVITA_URL` is not reachable from inside the Lector container: wrong host name, wrong
port, or the containers are not on the same Docker network.

**Fix.**

1. Test from inside the container (replace the URL with your `KAVITA_URL`):

   ```sh
   docker exec lector python -c "import urllib.request; print(urllib.request.urlopen('http://kavita:5000/api/health').status)"
   ```

   `200` means Kavita is reachable. "Name or service not known" means the host name does not
   resolve; "Connection refused" means the port is wrong or Kavita is down.
2. For `http://kavita:5000`: check which network Kavita is on
   (`docker inspect kavita --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'`)
   and use that name in `docker-compose.yml`. Use Kavita's internal port (5000), not its published
   host port.
3. Otherwise switch to `http://host.docker.internal:<published port>` (on Linux add the
   `extra_hosts` entry) or the LAN address. See
   [DEPLOYMENT.md](DEPLOYMENT.md#networking-how-lector-reaches-kavita).
4. A message `KAVITA_URL must start with http:// or https://` means exactly that.
5. Apply changes with `docker compose up -d`, then press **Sync Kavita now**.

## Kavita rejects the API key (401)

**Symptom.** The Kavita card says **Reachable** but shows no version. The last sync reads
`Kavita rejected the API key (HTTP 401); check KAVITA_API_KEY` (or `HTTP 403`). With a
username and password instead: `Kavita rejected the username/password (HTTP 401): ...`. If no
credentials are set at all: `Kavita is not configured (set KAVITA_API_KEY or KAVITA_USERNAME/KAVITA_PASSWORD)`.

**Cause.** The key is wrong, was regenerated in Kavita, belongs to a deleted or locked user, or
has stray spaces or quotes in `.env`.

**Fix.**

1. Copy the key again from Kavita (signed in as the Lector user: user menu, Settings, 3rd Party
   Clients). In `.env` write it bare: `KAVITA_API_KEY=abc123...`, no quotes.
2. Make sure the Kavita user has the **Login** role.
3. Test the key from the container (prints only the status code):

   ```sh
   docker exec lector python -c "import httpx,os; r=httpx.post(os.environ['KAVITA_URL']+'/api/Plugin/authenticate', params={'apiKey': os.environ['KAVITA_API_KEY'], 'pluginName': 'Lector'}); print(r.status_code)"
   ```

   `200` is good. If this prints 200 but Lector still fails, the container is running with old
   settings: `docker compose up -d`.

## Sync finds 0 books

**Symptom.** The library is empty; the sync message says `0 book file(s) in 0 series across 0 library(ies)`
or similar.

**Causes and fixes**, in order of likelihood:

1. **`0 library(ies)`: library type not imported.** Lector imports only the Kavita library types
   listed in `KAVITA_LIBRARY_TYPES` (default `2`, Book). Light novel libraries are type `4`; set
   `KAVITA_LIBRARY_TYPES=2,4`. Manga (0), Comic (1) and Image (3) libraries hold pictures and cannot
   be narrated. Run the "libraries the Kavita user can see" command from
   [Diagnostic tools](#diagnostic-tools) to see each library's type.
2. **`0 library(ies)`: library not shared with the Kavita user.** Kavita only returns libraries the
   user may access. In Kavita, edit the Lector user and tick the library, then sync again.
3. **Kavita itself lists few or no books: metadata parsing is off.** A Kavita library created
   with metadata disabled derives series from file names and can miss most EPUBs. In Kavita, turn on
   the library's **Enable Metadata** option (library settings, advanced tab), rescan the library, and
   sync Lector again.
4. **Series found but `0 book file(s)`.** Kavita returned series without usable files (for example a
   scan still in progress). Let Kavita finish scanning and sync again.
5. **Sync never ran.** With `SYNC_INTERVAL_MINUTES=0` there is no automatic sync, not even at
   start-up; press **Sync Kavita now**.

The library page does not refresh by itself after a sync; reload it.

## Books show "Missing"

**Symptom.** Book cards carry a red **Missing** label; the book page says **Missing from Kavita**.
The sync message counts them (`..., N missing`). The log has:

```
WARNING lector.sync: cannot access /books/Author/Title.epub (mapped /books/Author/Title.epub) and download failed: download chapter 12: HTTP 401 (the Kavita user needs the Download role): ...
```

**Cause.** Either the book was removed from Kavita, or Lector cannot open its file: the path Kavita
reports, translated by `KAVITA_PATH_MAP`, does not exist inside the Lector container, *and* the
fallback download through Kavita's API failed (usually because the Kavita user lacks the Download
role).

**Fix.**

1. See where Kavita mounts its books and what Lector sees:

   ```sh
   docker inspect kavita --format '{{json .Mounts}}'
   docker exec lector ls /books
   ```

2. `BOOKS_PATH` must be the same **host** folder Kavita mounts as `/books`. If Kavita uses a
   different container path, map it: `KAVITA_PATH_MAP=/kavita/path=/books`. Several roots are
   separated with `;`.
3. Check the file is readable by `PUID` (`docker exec -u lector lector ls -l "/books/Author/Title.epub"`).
4. Or, if you cannot mount the folder, give the Kavita user the **Download** role; Lector then keeps
   copies in `/data/cache/books`.
5. `docker compose up -d`, then **Sync Kavita now**. Missing flags are re-evaluated on every sync.

Books really removed from Kavita stay listed as missing, with their audio and progress kept. Delete
their audiobooks if you want the disk space back.

## Cover not showing

**Symptom.** A book shows the coloured placeholder with its title instead of a cover.

**Cause.** Lector copies covers from Kavita during sync, once per book, into
`/data/cache/covers/<book id>.jpg`. Kavita had no cover yet at that time, the cover download
failed, or you changed the cover in Kavita afterwards (Lector does not fetch it again by itself).
Browsers also cache covers for up to a day.

**Fix.** Delete the cached cover and sync; missing covers are fetched again:

```sh
docker exec lector rm /data/cache/covers/<book id>.jpg
```

Then press **Sync Kavita now** and reload the page (a hard refresh, see
[below](#old-interface-after-an-upgrade), if the browser still shows the old image). The book id is
the number in the book page's address (`#/book/<id>`).

## "Book file not found" error on an audiobook

**Symptom.** An audiobook fails immediately with
`Book file not found: '/books/Author/Title.epub' (check KAVITA_PATH_MAP / volume mounts)`.

**Cause.** The file path recorded at the last sync no longer exists inside the container: the
mount changed, the file moved in Kavita, or a downloaded copy in `/data/cache/books` was deleted.

**Fix.** Correct the mount or `KAVITA_PATH_MAP` as in [Books show "Missing"](#books-show-missing),
run **Sync Kavita now** (it records the current path), then **Retry** the audiobook.

## "Only EPUB books can be narrated right now"

**Symptom.** An audiobook for a PDF (or other non-EPUB) book fails with this error.

**Cause.** Lector imports every file in your Book libraries, but can only extract text from EPUBs.

**Fix.** Use an EPUB edition of the book. Delete the failed audiobook to tidy up the queue.

## Rendition stuck in queued, or "TTS engine not ready"

**Symptom.** An audiobook stays **Queued**, or fails with
`TTS engine not ready: ...`. The admin page's TTS engine card shows **not ready**. The log has
`ERROR lector.worker: TTS engine not ready: ...`.

**Causes and fixes.**

1. **Another audiobook is generating.** Only one is narrated at a time, oldest request first.
   Check the queue on the admin page; a paused or failed one does not block the queue.
2. **The model is still downloading** (first start). Watch for
   `INFO lector.tts: Downloading ... -> /models/kokoro-v1.0.onnx` and wait for
   `Kokoro model loaded from /models/kokoro-v1.0.onnx (54 voices)`.
3. **The download failed** (no internet, GitHub blocked): the error mentions a URL or network
   error. Download both files yourself from
   <https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0>
   (`kokoro-v1.0.onnx`, about 325 MB, and `voices-v1.0.bin`, about 28 MB) into
   `KOKORO_MODELS_PATH`, restart, and **Retry** the audiobook.
4. **The models folder is read-only or owned by someone else**: the error mentions
   `Permission denied` or `Read-only file system` for `/models/...`. Make `KOKORO_MODELS_PATH`
   writable by `PUID`, or put the model files there yourself.
5. **`KOKORO_AUTO_DOWNLOAD=false` and no files**: the error says
   `Kokoro model files missing (...) and KOKORO_AUTO_DOWNLOAD is off`.
6. **External engine** (`TTS_ENGINE=openai`): the server at `TTS_OPENAI_URL` is down or returns a
   5xx error on `/models`.
7. **The worker stopped.** Look for `worker start-up failed` or repeated
   `could not fetch next job` in the logs, then `docker compose restart lector`. Interrupted
   audiobooks are re-queued on start (`requeued N interrupted rendition(s)`).

## Pages spin for minutes on the first start

**Symptom.** Right after the first start, the book page or the admin page shows a spinner for
several minutes.

**Cause.** The Kokoro model is downloading. The voice list (book page) and the engine status (admin
page) wait until it is loaded.

**Fix.** Wait for `Kokoro model loaded ...` in the logs, or provide the model files yourself (see
the previous entry). The library page and the player work in the meantime.

## Very slow generation

**Symptom.** The admin page's realtime factor is low (for example below 2x on a recent desktop
CPU), or the host becomes sluggish.

**Causes and fixes.**

1. **Thread count.** onnxruntime's default thread setting can be much slower than a fixed number.
   On an Apple M4 in Docker Desktop, `TTS_THREADS=8` measured about 5x realtime against about 2.2x
   with the default. Set `TTS_THREADS` (start near the number of CPUs Docker gives the container),
   `docker compose up -d`, and check for `Kokoro using 8 onnxruntime threads` in the log. The
   realtime factor on the admin page is an average over the current job, so compare on a fresh
   one.
2. **Docker CPU limit.** Docker Desktop shares only the CPUs set under Settings, Resources. A
   `deploy.resources.limits.cpus` cap in `docker-compose.yml` also limits it; set `TTS_THREADS` to
   the same number.
3. **Emulated image on Apple silicon.** An amd64 image running under Rosetta or QEMU emulation is
   several times slower. Check:

   ```sh
   docker image inspect lector:local --format '{{.Architecture}}'   # expect arm64 on Apple silicon
   docker exec lector uname -m                                       # expect aarch64
   ```

   If it says `amd64`/`x86_64`, remove any `platform: linux/amd64` setting or `DOCKER_DEFAULT_PLATFORM`
   variable and rebuild: `docker compose build --no-cache && docker compose up -d`.
4. **Other heavy containers** (Kavita scans, transcoding) compete for the CPU.
5. **A slow CPU.** Consider an [external GPU TTS server](DEPLOYMENT.md#using-an-external-tts-server-gpu).

## Disk full during generation

**Symptom.** Chapters fail one after another; the audiobook ends with
`N chapter(s) failed: ...` mentioning `No space left on device` (from ffmpeg or the WAV writer). The
admin Storage card shows little free space. Other parts of Lector may fail with
`database or disk is full`.

**Cause.** Each chapter is written to a temporary WAV file (about 96 KB per second of audio, up to
roughly 300 MB for the longest chapters) before it is encoded, next to the audio files in `/data`.

**Fix.** Free space (delete unneeded audiobooks or M4B files, see
[DEPLOYMENT.md](DEPLOYMENT.md#storage-housekeeping)), then **Retry** the audiobook. Finished
chapters are kept; only the failed ones are redone. Temporary files are removed automatically.

## Permission denied on /data

**Symptom.** The container keeps restarting; the log shows `PermissionError: [Errno 13] Permission
denied: '/data/...'` or `sqlite3.OperationalError: unable to open database file`. Possibly also
`entrypoint: warning: could not chown /data`.

**Cause.** The app runs as `PUID`/`PGID` but the data folder belongs to another user (often root,
because Docker created it). Network shares with root squashing refuse the entrypoint's ownership
fix.

**Fix.**

1. Set `PUID`/`PGID` in `.env` to the owner you want (`id -u`, `id -g`).
2. Fix ownership on the host: `sudo chown -R 1000:1000 /path/to/lector-data` (and the models
   folder).
3. `docker compose up -d`.

See [DEPLOYMENT.md](DEPLOYMENT.md#linux-hosts-puid-and-pgid).

## M4B build fails

**Symptom.** After **Build M4B** you see "Building M4B in the background…", but the button later
returns to **Build M4B** and no download appears. The log has
`ERROR lector.api: m4b build failed for rendition N` followed by the reason, often
`ffmpeg failed (exit 1): ...`.

**Causes and fixes.**

- **Not enough disk space**: the M4B needs about as much space as the audiobook again. Free space
  and try again.
- **Chapter files missing** (for example after restoring a database without its audio): the error
  says `no finished chapters`. Delete and regenerate the audiobook.
- "No narrated chapters yet" means nothing has been generated.
- "Only admins can request narrations" means regular users may not build M4B files while
  requests are turned off.

## Phonemizer or synthesis warnings in the logs

**Symptom.** Messages such as `words count mismatch` (from the phonemizer), or
`WARNING lector.worker: synthesis failed (...); retrying with ASCII-only text`.

**Cause and fix.** The `words count mismatch` warning is harmless noise from the phonemizer and is
normally silenced; ignore it. A failed chunk is retried once with plain ASCII text; if that also
fails you see `synthesis failed again (...); skipping chunk: '...'` and a short silence is inserted.
Three failures in a row fail the chapter with `TTS failed on 3 consecutive chunks (last: '...')`;
**Retry** the audiobook, and if it keeps happening on the same text, report it with that line.

## Wrong pronunciation, wrong language, or accents read oddly

**Symptom.** A French book sounds like an English speaker reading French, names are mangled, or
accented letters come out strangely.

**Cause.** The **voice** decides the pronunciation rules: an English voice reads everything as
English. Separately, Lector cleans the text according to the **book's language** from its EPUB
metadata: in books marked English, accents are removed (`Dantès` becomes `Dantes`, `Æ` becomes
`AE`) so the English voice does not stumble over them. In books marked as another language the
accents are kept.

**Fix.**

- Generate the audiobook with a voice of the book's language (voices are grouped by language).
- If a book is marked with the wrong language, fix its `dc:language` in the EPUB (for example with
  Calibre's metadata editor), let Kavita rescan, sync Lector, and generate again.
- Some names and rare words will always be mispronounced; that is a limit of the voice model.

## Chapters split oddly, or the Contents page is narrated

**Symptom.** Chapters are merged, split into parts like "Chapter 12 (1/2)", a "Front matter"
chapter appears, or the table of contents is read aloud.

**Cause.** Lector builds chapters from the EPUB's table of contents. If the table of contents has
fewer than two usable entries it falls back to the book's headings, and then to one chapter per
file inside the EPUB. On top of that:

- pieces shorter than 250 characters (a heading on its own page) are merged into the next chapter;
- chapters longer than about 48,000 characters (about 50 minutes) are split into numbered parts;
- text before the first chapter becomes "Front matter";
- sections titled Contents, Table of Contents, Illustrations, List of Illustrations or Index, or made
  up mostly of links, are skipped. A plain-text contents page with another title is not
  recognised and gets narrated.

**Fix.**

1. Look at the plan Lector builds:

   ```sh
   docker exec lector python -m app.epub_text "/books/Author/Title.epub"
   ```

   It prints one line per chapter with its size and title; `SKIP` marks skipped ones.
2. Fix the EPUB's table of contents (Calibre's ebook editor has a table-of-contents tool), let Kavita
   rescan, and sync Lector.
3. Delete the existing audiobook and generate it again. (An audiobook still in progress notices
   changed text and redoes the affected chapters; a finished one does not.)

## Audio plays but seeking fails

**Symptom.** Audio plays from the start, but dragging the scrubber, skipping or resuming in the
middle of a chapter jumps back to 0:00 or stalls. Often only through a reverse proxy.

**Cause.** Seeking relies on HTTP `Range` requests answered with `206 Partial Content`. Something
between the browser and Lector (a caching proxy, a CDN, a misconfigured proxy) strips the `Range`
header or returns the full file.

**Fix.** Test through the same address the browser uses:

```sh
TOKEN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"username":"you","password":"your-password"}' \
  https://lector.example.com/api/auth/login | python3 -c 'import sys, json; print(json.load(sys.stdin)["token"])')
curl -s -o /dev/null -D - -H "Authorization: Bearer $TOKEN" -H 'Range: bytes=0-99' \
  https://lector.example.com/api/renditions/1/chapters/0/audio
```

Expect `HTTP/... 206`, `Accept-Ranges: bytes` and `Content-Range: bytes 0-99/<size>`. Try the same
against `http://<host>:5055` directly: if that gives 206 and the proxy gives 200, fix the proxy (no
caching on `/api`, forward `Range`; see the
[proxy examples](DEPLOYMENT.md#reverse-proxy-and-https)). Use an audiobook id and chapter that
exist (`1` and `0` above are examples).

## "Could not load this chapter's audio."

**Symptom.** The player shows this message for a chapter marked done.

**Cause.** The chapter's file is gone from `/data/audio` (deleted by hand, a database restored
without its audio, or a data folder mounted at a different container path). The server answers
`Chapter audio not available yet`.

**Fix.** Check the folder is mounted where it was (`/data`). If the files are really gone, delete
the audiobook and generate it again.

## Progress not saving

**Symptom.** You reopen a book and it starts from an older position, or another device does not
pick up where you stopped.

**Causes and fixes.**

- **Different audiobook.** Progress is kept per audiobook (voice and speed). Two narrations of the
  same book have separate positions.
- **Different account.** Progress is per user.
- **Two devices open at once.** Each device saves its own position every 10 seconds while playing;
  the last one to save wins. The player loads your position when you open it, so reopen the
  audiobook on the second device after stopping on the first.
- **Session expired or cookies blocked.** Progress is saved silently; if you are signed out it
  cannot be. Sign in again; see also
  [the next entry](#sign-in-succeeds-but-bounces-back-to-the-sign-in-page).
- **Closed too quickly.** Lector also saves when the page is hidden or closed, but a phone that
  kills the browser instantly can lose the last few seconds.

To see what the server has stored:

```sh
docker exec lector python -c "import sqlite3; db=sqlite3.connect('/data/lector.db'); [print(r) for r in db.execute('select u.username, p.rendition_id, p.chapter_index, round(p.position), p.finished, p.updated_at from progress p join users u on u.id=p.user_id order by p.updated_at desc limit 10')]"
```

## Signed out after changing LECTOR_SECRET_KEY

**Symptom.** After changing `LECTOR_SECRET_KEY` (or deleting `/data/secret.key`), everyone sees
"Your session expired - please sign in again."

**Cause.** Session cookies are signed with this key; a new key invalidates every existing session.
Passwords are not affected.

**Fix.** Sign in again with your usual password. If signing in itself does not stick, see the next
entry. To keep sessions across reinstalls, back up `secret.key` or set a fixed
`LECTOR_SECRET_KEY`.

## Sign-in succeeds but bounces back to the sign-in page

**Symptom.** You enter the right password, the library flashes, and you are back at the sign-in
page with "Your session expired - please sign in again."

**Cause.** The browser did not keep the session cookie. Almost always `COOKIE_SECURE=true` while you
open Lector over plain `http://`: the cookie is marked HTTPS-only and the browser drops it. A proxy
that strips `Set-Cookie` headers has the same effect.

**Fix.** Use the `https://` address, or set `COOKIE_SECURE=false` and run `docker compose up -d`.
A wrong password shows "Invalid username or password" instead.

## "Passwords do not match" and other form messages

| Message | Where | Meaning |
|---|---|---|
| Passwords do not match. | Setup page | The two password fields differ. Retype both (password managers sometimes fill only one). |
| New passwords do not match. | Settings, Change password | "New password" and "Confirm new password" differ. |
| Current password is incorrect | Settings, Change password | The current password is wrong. |
| Username must be 2–64 characters: letters, digits, _ . - | Setup, registration, Add user | Spaces and other characters are not allowed. |
| Password must be at least 6 characters. | Anywhere a password is set | Too short. |
| password: password must be at most 72 bytes | Anywhere a password is set | Too long (non-ASCII characters count as several bytes). |
| Username already taken | Registration, Add user | Usernames are unique regardless of upper/lower case. |
| Cannot demote the last admin | Admin, Users | Make someone else an admin first. |

## Forgotten admin password

If another admin can sign in, they can set a new password on the admin page (Users, **Password**).
Otherwise reset it in the database (change `admin` and the new password; drop `-u lector` if you
run without `PUID`):

```sh
docker exec -i -u lector lector python - <<'EOF'
import sqlite3, bcrypt
user, new_password = "admin", "a-new-password"
h = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt(12)).decode()
db = sqlite3.connect("/data/lector.db")
n = db.execute("UPDATE users SET password_hash=? WHERE lower(username)=lower(?)", (h, user)).rowcount
db.commit()
print(n, "user(s) updated")
EOF
```

To make an existing regular user an admin instead, run the same way:
`UPDATE users SET is_admin=1 WHERE lower(username)=lower('bob')`.

## Settings in .env have no effect

**Symptom.** You changed a value in `.env` but Lector behaves as before.

**Causes and fixes.**

- **The container was not recreated.** `docker compose restart` keeps the old environment. Run
  `docker compose up -d`.
- **The value is overridden in the database.** `DEFAULT_VOICE`, `DEFAULT_SPEED`,
  `ALLOW_USER_REQUESTS` and `ALLOW_REGISTRATION` are only defaults; once an admin has saved the
  **Server settings** form, the saved values win. Change them on the admin page instead.

## Old interface after an upgrade

**Symptom.** After updating Lector, the web app still shows old screens, buttons are missing, or
something behaves as before the update.

**Cause.** The browser (or the installed app's service worker) is still using cached files. Lector
always asks the server first, so this is rare, but browsers can hold on to old script files.

**Fix.**

1. Hard refresh: Ctrl+Shift+R (Windows, Linux) or Cmd+Shift+R (macOS).
2. Installed app (PWA): close it completely (on a phone, swipe it away from the app switcher) and
   open it again.
3. Still stuck: in a desktop browser open the developer tools, go to Application, Service
   workers, click **Unregister**, then use **Clear site data** and reload. On a phone, clear the
   site's data in the browser settings. You will need to sign in again.

## Reporting a bug

Open an issue at <https://github.com/tahmidMahmud/lector/issues> with:

- the Lector version (`curl -s http://<host>:5055/api/setup` shows it) and how you run it
  (Docker, systemd, host OS and CPU);
- the Kavita version (admin page);
- what you did, what you expected, what happened, and any message the UI showed;
- the relevant log lines (`docker compose logs --since 30m lector`), with your Kavita API key and
  host names removed;
- for chapter or text problems, the output of `python -m app.epub_text` for the book (and, if you
  can, where the EPUB came from, for example a Project Gutenberg ebook number).
