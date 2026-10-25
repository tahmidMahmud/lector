# Deploying Lector

This guide covers installing and running Lector for real: planning hardware and disk, connecting
it to Kavita safely, networking, HTTPS, updates, backups and security. The short version is in the
[README](../README.md#quick-start-docker); every environment variable is described in
[CONFIGURATION.md](CONFIGURATION.md); problems are covered in
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Contents

- [Requirements](#requirements)
- [Prepare Kavita](#prepare-kavita)
- [Compose setup](#compose-setup)
- [Networking: how Lector reaches Kavita](#networking-how-lector-reaches-kavita)
- [First start](#first-start)
- [Verify the sync](#verify-the-sync)
- [Reverse proxy and HTTPS](#reverse-proxy-and-https)
- [Linux hosts: PUID and PGID](#linux-hosts-puid-and-pgid)
- [Running on a NAS](#running-on-a-nas)
- [Updating](#updating)
- [Backups and restore](#backups-and-restore)
- [Moving the data folder](#moving-the-data-folder)
- [Storage housekeeping](#storage-housekeeping)
- [Resource limits](#resource-limits)
- [Running without Docker (systemd)](#running-without-docker-systemd)
- [Security checklist](#security-checklist)
- [Using an external TTS server (GPU)](#using-an-external-tts-server-gpu)

## Requirements

- **Docker** with Compose v2 (`docker compose ...`). Docker Desktop on macOS or Windows works; the
  image builds natively for the host architecture (amd64 or arm64).
- **A running Kavita** (tested with 0.9.x) with at least one **Book** library containing EPUB
  files.
- **Internet access once**, to download the Kokoro model (about 350 MB) on first start - or
  download it yourself (see [TROUBLESHOOTING.md](TROUBLESHOOTING.md#rendition-stuck-in-queued-or-tts-engine-not-ready)).
  After that Lector works fully offline.

### CPU

Narration runs on the CPU and uses every core it is allowed to. Speed is measured as a
*realtime factor*: 5x means one hour of audio takes 12 minutes to generate.

| Setup (measured) | Realtime factor |
|---|---|
| Apple M4, Docker Desktop, `TTS_THREADS=8` | about 5x |
| Apple M4, Docker Desktop, default threads | about 2.2x |

Other CPUs scale roughly with their single-core speed and number of fast cores. Anything above 1x
generates faster than you listen, so you can start a book as soon as the first chapter is ready.
Slow, low-power CPUs (many NAS boxes) may manage less than that; in that case consider an
[external TTS server](#using-an-external-tts-server-gpu). The admin page shows the live
realtime factor, so you can measure your own machine with a short book.

### Memory

Lector idles at about 600 MB with the Kokoro model loaded (measured). Leave at least 2 GB for the
container while it is narrating. Only one book is narrated at a time, so memory does not grow
with the queue.

### Disk planning

| Item | Size |
|---|---|
| Docker image | about 1.2 GB |
| Kokoro model files (`kokoro-v1.0.onnx` + `voices-v1.0.bin`) | about 353 MB |
| Audio at the default 64 kbps mono MP3 | about **29 MB per hour** of audio |
| One 15-hour novel | about 440 MB |
| 100 books of 10 hours each | about 29 GB |
| An M4B export | about the same size again as the book's chapter audio |
| Temporary WAV while a chapter is encoded | about 96 KB per second of that chapter; chapters are capped near 50 minutes, so under 300 MB |
| Database, covers, voice previews | a few MB |
| Downloaded book copies (only if the books folder is not mounted) | the size of your EPUBs |

Audio size scales with `AUDIO_BITRATE`: roughly 0.45 MB per hour for each kbps (96k gives about
43 MB per hour). Put `LECTOR_DATA_PATH` on a disk with room to grow.

## Prepare Kavita

Lector talks to Kavita's REST API with a Kavita **API key**. A Kavita API key carries **all the
roles of the user it belongs to** - an admin's key can change your whole Kavita server. Give
Lector its own low-privilege Kavita user instead.

### Create a dedicated Kavita user

Exact labels vary a little between Kavita versions; these are for 0.9.x.

1. Sign in to Kavita as an admin and open **Settings**. In the server section, open **Users**.
2. Click **Invite** (Kavita's way of adding a user). Enter an email address. If Kavita has no
   email server configured it shows an invite link instead of sending mail; copy it.
3. Roles: grant **Login** only. Add **Download** *only* if you will not mount the books folder into
   Lector (Lector then downloads private copies of the EPUBs through the API).
4. Libraries: tick every **Book** library you want narrated. Lector only sees libraries shared with
   this user.
5. Open the invite link in a private browser window, choose a username such as `lector` and a
   password, and finish the sign-up.

### Get the API key

1. Sign in to Kavita **as the new user**.
2. Open the user menu (top right) and choose **Settings**.
3. Open **3rd Party Clients** (the same key is shown in the OPDS section, inside the OPDS URL).
4. Copy the **API key** into `KAVITA_API_KEY` in Lector's `.env`.

Lector exchanges the key for a session token through Kavita's `/api/Plugin/authenticate`.
Alternatively leave `KAVITA_API_KEY` empty and set `KAVITA_USERNAME` and `KAVITA_PASSWORD` for the
same user.

### Check the library's metadata setting

Kavita libraries created with metadata parsing turned off derive series from file names and can
miss most EPUBs entirely. Open the library's settings in Kavita (the **Enable Metadata** option on
the advanced tab; `enableMetadata` in the API), make sure it is on, and rescan the library before
the first Lector sync. If Kavita itself does not list your books, Lector cannot either.

## Compose setup

1. Get the source and create your configuration:

   ```sh
   git clone https://github.com/tahmidMahmud/lector.git
   cd lector
   cp .env.example .env
   chmod 600 .env          # it will hold your Kavita API key
   ```

2. Edit `.env`. The three host paths:

   | Variable | Mounted at | What to point it at |
   |---|---|---|
   | `LECTOR_DATA_PATH` | `/data` (read-write) | An empty folder on a big disk. It receives the database, session key, covers, chapter audio and M4B files. |
   | `BOOKS_PATH` | `/books` (read-only) | The **same host folder that Kavita mounts as `/books`**. Lector opens the EPUBs here directly. Check Kavita's `volumes:` to find it. |
   | `KOKORO_MODELS_PATH` | `/models` | A folder holding `kokoro-v1.0.onnx` and `voices-v1.0.bin`, or an empty **writable** folder so Lector can download them. Default `./models`. |

   If Kavita mounts its books somewhere other than `/books`, or uses several folders, set
   `KAVITA_PATH_MAP` so Kavita's paths translate to Lector's (see the
   [README](../README.md#path-mapping-kavita_path_map)).

3. Set `KAVITA_URL` and `KAVITA_API_KEY` (see [networking](#networking-how-lector-reaches-kavita)).

4. On Linux, set `PUID`/`PGID` (see [below](#linux-hosts-puid-and-pgid)). The example values
   `501`/`20` are for the first user on macOS.

5. Optional: `TTS_THREADS` (try 8 on an Apple M4; see [resource limits](#resource-limits)),
   `LECTOR_PORT` (host port, default 5055) and `TZ`.

6. Create the data folder yourself so Docker does not create it owned by root:

   ```sh
   mkdir -p /path/to/lector-data
   ```

Settings in `.env` are read when the container is created. After editing `.env`, apply it with
`docker compose up -d` (a plain `docker compose restart` keeps the old values).

## Networking: how Lector reaches Kavita

`KAVITA_URL` is Kavita's address **as seen from inside the Lector container**. Pick one:

| Option | `KAVITA_URL` | When to use |
|---|---|---|
| Same Docker network | `http://kavita:5000` | Kavita runs in Docker on the same host. Best option: traffic stays on the Docker network. |
| Host gateway | `http://host.docker.internal:<kavita host port>` | Kavita is on the same host but on another network, or not in Docker. |
| LAN address | `http://192.168.1.20:5000` | Kavita runs on another machine. |

**Same network.** The shipped `docker-compose.yml` joins an external network named
`media-server_default`. Find the network your Kavita container is on and put that name in both
`networks:` blocks:

```sh
docker inspect kavita --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

`kavita` in the URL must be Kavita's compose service name or container name; `5000` is Kavita's
port inside its container (not the published host port).

**Host gateway.** Delete both `networks:` blocks from `docker-compose.yml`. Docker Desktop
(macOS, Windows) provides `host.docker.internal` automatically. On Linux, add this to the `lector`
service:

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Use Kavita's **published host port** here (5000 unless you mapped it to something else, such as
5050).

**LAN address.** Delete the `networks:` blocks and use the other machine's IP and Kavita's
published port. With this option Lector usually cannot mount the books folder directly; either
mount the same share into Lector at the path Kavita reports, or give the Kavita user the
**Download** role and let Lector download copies into `/data/cache/books`.

## First start

```sh
docker compose up -d --build
docker compose logs -f lector
```

The access log prints a line for every API request. To see only Lector's own messages:

```sh
docker compose logs -f lector | grep -vE '"(GET|HEAD|POST|PUT|DELETE) /'
```

A healthy first start looks like this (timestamps trimmed):

```
INFO lector: Lector 0.1.0 started (data dir /data)
INFO lector.tts: Downloading https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx -> /models/kokoro-v1.0.onnx
INFO lector.tts: Downloading https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin -> /models/voices-v1.0.bin
INFO lector.tts: Kokoro using 8 onnxruntime threads
INFO lector.tts: Kokoro model loaded from /models/kokoro-v1.0.onnx (54 voices)
INFO lector.worker: TTS engine kokoro warmed up in 0.4s
INFO lector.sync: sync: {'status': 'ok', 'books': 8, 'series': 7, 'libraries': 1, 'new': 8, ...}
```

- The two `Downloading` lines appear only when the model files are missing. The download happens
  at start-up and can take a few minutes; until it finishes, the book and admin pages keep
  loading.
- `Kokoro using N onnxruntime threads` appears only when `TTS_THREADS` is set.
- `TTS engine not ready: ...` (an ERROR) means the model could not be loaded or downloaded; the
  message says why.
- Entrypoint lines such as `entrypoint: chown -R 1000:1000 /data` mean it fixed folder ownership
  for `PUID`/`PGID`.

Then open `http://<host>:5055`, create the admin account and follow the
[user guide](USER-GUIDE.md).

The container has a health check on `/api/health`; `docker ps` shows `(healthy)` once it answers.

## Verify the sync

1. Open the **Admin** page. The **Kavita** card should say **Reachable** and show Kavita's
   version. Reachable without a version means Kavita answers but rejects Lector's login.
2. The **Kavita library sync** card shows the last sync message, for example
   `8 book file(s) in 7 series across 1 library(ies); 8 new, 0 missing`.
3. Press **Sync Kavita now** to run it again; reload the library page to see the books.

What the numbers tell you:

| Message says | Meaning |
|---|---|
| `0 library(ies)` | No library of the types in `KAVITA_LIBRARY_TYPES` (default `2`, Book) is shared with the Kavita user. |
| `N series` but `0 book file(s)` | Kavita returned series without files Lector can use; check the library in Kavita. |
| `M missing` | M books cannot be read or downloaded, or vanished from Kavita. |
| `... error(s): ...` | Some libraries or series failed; the reasons follow. |

Details for each case are in [TROUBLESHOOTING.md](TROUBLESHOOTING.md#sync-finds-0-books).

## Reverse proxy and HTTPS

Put Lector behind a reverse proxy when you want HTTPS (needed to install it as a full app on
phones, and for anything outside your home network).

What Lector needs from a proxy:

- **Serve it at the root of a host name**, such as `https://lector.example.com/`. Lector's pages
  use absolute paths (`/api`, `/static`), so a sub-path like `https://example.com/lector/` does
  not work.
- **Pass `Range` requests through untouched.** The player seeks by requesting byte ranges of the
  chapter files and expects `206 Partial Content`. Don't put a caching layer in front of `/api`.
- **No large uploads**: nothing is ever uploaded, so default body-size limits are fine.
- **No WebSockets**: the UI polls over plain HTTP.
- Set `COOKIE_SECURE=true` in `.env` once Lector is only reached over HTTPS, and apply it with
  `docker compose up -d`. Don't set it while you still use plain `http://` addresses - the browser
  then drops the session cookie and you cannot stay signed in.

When the proxy runs on the same host, publish Lector only on localhost so nobody can bypass the
proxy. In `docker-compose.yml`:

```yaml
    ports:
      - "127.0.0.1:${LECTOR_PORT:-5055}:5000"
```

### Caddy

Caddy obtains certificates automatically and passes `Range` headers by default:

```caddyfile
lector.example.com {
    reverse_proxy 127.0.0.1:5055
}
```

### nginx

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name lector.example.com;

    ssl_certificate     /etc/letsencrypt/live/lector.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/lector.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5055;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Audio seeking: forward byte ranges and stream responses as they come.
        proxy_set_header Range $http_range;
        proxy_set_header If-Range $http_if_range;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name lector.example.com;
    return 301 https://$host$request_uri;
}
```

On nginx older than 1.25.1 write `listen 443 ssl http2;` instead of the separate `http2 on;`.
If Lector runs in the same Docker network as the proxy, use `http://lector:5000` as the upstream.

To check that seeking will work through the proxy, see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#audio-plays-but-seeking-fails).

## Linux hosts: PUID and PGID

The container starts as root. When `PUID` is set, the entrypoint re-maps its built-in `lector`
account to `PUID`/`PGID`, makes sure `/data` and `/models` belong to that account, and runs the
app as it. With `PUID` empty the app runs as root and creates root-owned files in your data
folder.

1. Find your ids: `id -u` and `id -g` (often `1000` and `1000`).
2. Set them in `.env`:

   ```ini
   PUID=1000
   PGID=1000
   ```

3. Create the data and models folders as that user before the first start, then
   `docker compose up -d`.

The entrypoint changes ownership only when the top-level folder has the wrong owner, so a large
audio library is not re-chowned on every start. If the chown fails (for example on a
root-squashed network share) you see `entrypoint: warning: could not chown /data` and must fix
the ownership on the storage side. The books folder is mounted read-only and only needs to be
readable by `PUID`.

## Running on a NAS

Lector runs on any NAS that can run Docker Compose (Synology Container Manager projects, Unraid
with the Compose plugin, TrueNAS apps, plain Docker on OpenMediaVault and so on).

- **PUID/PGID**: use the ids of the user that owns your shares (on Synology, run `id <user>` over
  SSH; Unraid's default is `99`/`100`).
- **Keep the database on local storage.** SQLite (in WAL mode) is not reliable on SMB or NFS
  mounts. If your big disk is a network share, keep `/data` local and move only the audio there:
  add a second volume, for example `- /mnt/share/lector-audio:/audio`, and set
  `LECTOR_AUDIO_DIR=/audio` in `.env` **before** generating anything (file locations are stored
  in the database).
- **Books folder**: mount the share Kavita uses, read-only, at the path Kavita reports (or adjust
  `KAVITA_PATH_MAP`).
- **CPU**: many NAS CPUs are slow for neural TTS. Generate a short story first and read the
  realtime factor on the admin page. Below about 1x, consider an
  [external TTS server](#using-an-external-tts-server-gpu) on a faster machine.
- On ARM NAS models the image builds natively for arm64.

## Updating

```sh
cd lector
git pull
docker compose up -d --build
docker image prune -f        # optional: remove the old image
```

What survives an update: everything under `/data` (database with users, books, audiobooks,
progress, bookmarks and settings; the session key; audio; M4B files; covers) and the model files
in `/models`. Missing database tables are created automatically at start-up; there is no
migration tool, so take a [backup](#backups-and-restore) of `lector.db` before upgrading.

If a narration was running, it is re-queued on start-up and continues with the interrupted
chapter. After an update, reload the web app; if the old interface lingers, see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#old-interface-after-an-upgrade).

## Backups and restore

Everything is in `LECTOR_DATA_PATH` (`/data` in the container):

| Path | Contents | Back up? |
|---|---|---|
| `lector.db` (+ `lector.db-wal`, `lector.db-shm` while running) | Users, books, audiobook and chapter status, progress, bookmarks, server settings | **Yes - the important part.** Small. |
| `secret.key` | Session-signing key (unless you set `LECTOR_SECRET_KEY`) | Yes. Losing it only signs everyone out. |
| `audio/<book id>/<audiobook id>/` | Chapter files (`000.mp3`, ...), read-along timings (`000.json`, ...), `audiobook.m4b` | Optional: large, but regenerating takes hours. |
| `cache/` | Covers, voice previews, downloaded book copies | No; rebuilt automatically. |

Your `.env` file is outside the data folder; keep a copy of it too.

### Back up while running

The database uses SQLite's WAL mode, so copying `lector.db` alone while Lector runs can miss recent
changes. Use SQLite's backup API instead:

```sh
docker exec -u lector lector python -c "import sqlite3; s=sqlite3.connect('/data/lector.db'); d=sqlite3.connect('/data/lector-backup.db'); s.backup(d); d.close()"
```

Then copy `lector-backup.db`, `secret.key` and (optionally) `audio/` off the machine. Leave out
`-u lector` if you run without `PUID`.

### Back up offline

```sh
docker compose stop
rsync -a /path/to/lector-data/ /backup/lector-data/
docker compose start
```

### Restore

1. `docker compose stop`.
2. Put the files back into `LECTOR_DATA_PATH`. When restoring from a `lector-backup.db`, rename
   it to `lector.db` and delete any leftover `lector.db-wal` and `lector.db-shm`.
3. Make sure the files belong to `PUID`/`PGID` (`chown -R 1000:1000 /path/to/lector-data`).
4. `docker compose up -d`.

A database restored without its `audio/` folder still lists finished audiobooks, but their chapters
cannot be played ("Could not load this chapter's audio."). Delete those audiobooks on their book
pages and generate them again. Audio files without the database are of no use to Lector; it does
not re-import them.

## Moving the data folder

File locations are stored in the database as **container** paths (`/data/audio/...`), so you can
move the host folder freely as long as it stays mounted at `/data`:

1. `docker compose stop`
2. `rsync -a /old/lector-data/ /new/lector-data/` (or `mv` on the same disk).
3. Change `LECTOR_DATA_PATH` in `.env`.
4. `docker compose up -d`, check that an audiobook plays, then remove the old folder.

Do not change the container paths (`LECTOR_DATA_DIR`, `LECTOR_AUDIO_DIR`) for an existing
installation; finished chapters would point at paths that no longer exist.

## Storage housekeeping

- **See usage**: the admin page's **Storage** card shows the size of all audio and the free space.
  From the shell: `du -sh /path/to/lector-data/audio/*` (folders are named by book id).
- **Delete audiobooks** you no longer need: an admin opens the book page and clicks **Delete** on
  the audiobook card. This removes its audio, M4B and everyone's progress and bookmarks for it.
- **Delete just an M4B**: there is no button for this. Remove the file and the book page shows
  **Build M4B** again:

  ```sh
  rm /path/to/lector-data/audio/<book id>/<audiobook id>/audiobook.m4b
  ```

  To find the ids of a book:

  ```sh
  docker exec lector python -c "import sqlite3; db=sqlite3.connect('/data/lector.db'); [print(r) for r in db.execute('select b.id, r.id, b.title, r.voice, r.speed from renditions r join books b on b.id=r.book_id order by b.title')]"
  ```

- **Books removed from Kavita** are marked missing but keep their audio. Delete their audiobooks
  if you want the space back.
- **`cache/`** can be emptied at any time while Lector is stopped. Covers and downloaded book
  copies come back on the next sync, voice previews on the next preview.

## Resource limits

Narration deliberately uses every CPU core the container can see. To leave room for other
services:

- **`TTS_THREADS`** sets the number of onnxruntime threads. It is also the main performance knob:
  on an Apple M4 under Docker Desktop, `TTS_THREADS=8` measured about 5x realtime against about
  2.2x with the default. Try a value near the number of CPUs Docker gives the container (or the
  number of performance cores) and compare the realtime factor on the admin page.
- **CPU and memory caps** in `docker-compose.yml` (already there, commented out):

  ```yaml
      deploy:
        resources:
          limits:
            cpus: "6"
            memory: 3G
  ```

  A CPU cap does not change how many cores the container *sees*, so also set `TTS_THREADS` to the
  same number; otherwise onnxruntime starts more threads than it may run.
- **Docker Desktop** (macOS, Windows): the CPUs and memory available to all containers are set in
  Docker Desktop's Settings, under Resources.
- Pause the queue from the admin page if you need the CPU back for a while.

## Running without Docker (systemd)

Lector is a normal Python 3.12 application. On a Debian or Ubuntu host:

1. Install the system packages and create a service account:

   ```sh
   sudo apt-get install python3.12 python3.12-venv ffmpeg libsndfile1
   sudo useradd --system --home /var/lib/lector --create-home lector
   ```

2. Install the code and its dependencies:

   ```sh
   sudo git clone https://github.com/tahmidMahmud/lector.git /opt/lector
   cd /opt/lector
   sudo python3.12 -m venv .venv
   sudo .venv/bin/pip install -r requirements.txt
   ```

3. Create `/etc/lector.env` (readable only by root and `lector`) with native paths:

   ```ini
   LECTOR_DATA_DIR=/var/lib/lector
   KOKORO_MODEL=/var/lib/lector/models/kokoro-v1.0.onnx
   KOKORO_VOICES=/var/lib/lector/models/voices-v1.0.bin
   KAVITA_URL=http://localhost:5000
   KAVITA_API_KEY=your-key-here
   # Kavita reports /books/...; the files are in /srv/books on this machine:
   KAVITA_PATH_MAP=/books=/srv/books
   TTS_THREADS=8
   ```

4. Create `/etc/systemd/system/lector.service`:

   ```ini
   [Unit]
   Description=Lector audiobook server
   After=network-online.target
   Wants=network-online.target

   [Service]
   User=lector
   Group=lector
   WorkingDirectory=/opt/lector
   EnvironmentFile=/etc/lector.env
   # Exactly one worker: the narration queue runs inside the app process.
   ExecStart=/opt/lector/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 5055 --workers 1
   Restart=on-failure
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

5. Start it and follow the logs:

   ```sh
   sudo systemctl daemon-reload
   sudo systemctl enable --now lector
   journalctl -u lector -f
   ```

Never run more than one uvicorn worker or more than one instance against the same data folder.
For running from a source checkout while developing, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Security checklist

Lector is built for a home network. **Do not publish port 5055 directly to the internet.** If you
want to listen away from home, prefer a VPN (WireGuard, Tailscale and similar) so Lector stays
private; if you must expose it, put it behind an HTTPS reverse proxy and go through this list:

- [ ] HTTPS only, with `COOKIE_SECURE=true`.
- [ ] The container port is published on `127.0.0.1` only, so the proxy cannot be bypassed.
- [ ] Strong passwords for every account, especially admins. Lector has no login rate limiting or
      lockout; add rate limiting at the proxy (nginx `limit_req` on `/api/auth/login`) or use
      fail2ban, or put the proxy's own authentication in front.
- [ ] Open registration is off (Admin, Server settings) unless you really want strangers to sign
      up.
- [ ] "Allow regular users to request audiobooks" is off if untrusted users could fill your disk
      and occupy your CPU.
- [ ] Lector uses a dedicated low-privilege Kavita user (Login role; Download only if needed), not
      an admin's API key.
- [ ] `.env` is readable only by you (`chmod 600 .env`); it contains the Kavita API key.
- [ ] You know how to end sessions: sessions last `SESSION_DAYS` (30) days and survive password
      changes. Deleting a user ends their sessions; changing `LECTOR_SECRET_KEY` (or deleting
      `/data/secret.key` and restarting) signs everyone out.
- [ ] The API schema at `/api/docs` is public (the endpoints themselves still require a session).
      Block `/api/docs` and `/api/openapi.json` at the proxy if you prefer.
- [ ] You keep Lector updated and have a [backup](#backups-and-restore).

## Using an external TTS server (GPU)

The Lector image has no CUDA support; the built-in engine runs Kokoro on the CPU. For much faster
narration, run an OpenAI-compatible speech server on a machine with a GPU, for example
[Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) (follow its README for the GPU
container), and point Lector at it:

```ini
TTS_ENGINE=openai
TTS_OPENAI_URL=http://gpu-box:8880/v1
TTS_OPENAI_KEY=not-needed
TTS_OPENAI_MODEL=kokoro
```

Apply with `docker compose up -d`. How it works:

- Lector sends each text chunk to `POST {TTS_OPENAI_URL}/audio/speech` with the model, voice and
  speed and asks for WAV audio.
- The voice list comes from `{TTS_OPENAI_URL}/audio/voices` (Kokoro-FastAPI provides it). If that
  fails, Lector offers a short built-in list (Heart, Bella, Michael, Emma, George).
- The admin page's TTS engine card says **openai · ready** when `{TTS_OPENAI_URL}/models` answers.
- Voices are grouped by Kokoro's naming scheme (`af_heart` = American, female). Servers with other
  voice names (such as `alloy`) still work but are listed under American English.
- Each audiobook records the engine it was made with. Switching engines does not touch existing
  audiobooks; new ones use the new engine. `KOKORO_MODELS_PATH` must still be set because the
  compose file mounts it, but the model files are not needed.

Keep the TTS server on your private network; Lector sends it the full text of your books.
