# Frequently asked questions

Short answers, with links to the longer guides: [USER-GUIDE.md](USER-GUIDE.md),
[DEPLOYMENT.md](DEPLOYMENT.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md) and
[CONFIGURATION.md](CONFIGURATION.md).

## Books and text

### Which book formats can Lector narrate?

EPUB only. Lector imports every file in your Kavita Book libraries, so PDFs show up in the library
too, but generating them fails with "Only EPUB books can be narrated right now". Comics, manga and
image libraries (CBZ, CBR and so on) are not imported at all: there is no text to read.

### What about DRM-protected books?

Not supported. Lector reads the EPUB's text directly, which is encrypted in DRM-protected files
(Kavita cannot open those either). Use DRM-free EPUBs.

### Does it only work with Project Gutenberg books?

No. Any DRM-free EPUB works. Gutenberg books get one extra step: Lector recognises them and
narrates only the text between Gutenberg's `*** START OF ...` and `*** END OF ...` markers. Other
books are narrated from start to finish, minus tables of contents, illustration lists and indexes.

### What exactly does the Gutenberg trimming remove, and why?

Everything before the `*** START OF THE PROJECT GUTENBERG EBOOK ... ***` line (the legal header
with the ebook number, release date and so on) and everything from the
`*** END OF THE PROJECT GUTENBERG EBOOK ... ***` line onwards (the multi-page Project Gutenberg
License). Nobody wants five minutes of license text read aloud at the start and end of every book.
The book page shows a "Project Gutenberg text, boilerplate trimmed" badge for books identified as
Gutenberg ebooks. Credits that sit inside the markers, such as "Produced by ...", are still read.

### Can I share audio I made from Gutenberg books?

Lector is for personal listening, and this is not legal advice. A few facts to check against your
situation: most Gutenberg books are public domain **in the United States**, but not necessarily in
your country. "Project Gutenberg" is a trademark, and its license allows free use of the text when
you remove the Project Gutenberg name and license. Lector's trimming keeps the header and license
out of the audio, but text inside the markers (such as producer credits) can still mention Project
Gutenberg. If you plan to publish audio, listen to the beginning and end yourself and read the
license on gutenberg.org.

### Why are some chapters merged or split?

Chapters follow the EPUB's table of contents (falling back to headings, then to the files inside
the EPUB). Tiny pieces, such as a part title on its own page, are merged into the next chapter;
chapters longer than about 50 minutes of audio are split into numbered parts like "(1/2)"; text
before the first chapter becomes "Front matter". See
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#chapters-split-oddly-or-the-contents-page-is-narrated) to
inspect and fix a book's chapter plan.

## Voices and quality

### Which languages and voices are there?

All Kokoro v1.0 voices: American English, British English, Spanish, French, Hindi, Italian,
Japanese, Brazilian Portuguese and Mandarin Chinese, female and male (54 voices in total). Pick a
voice of the book's language; the voice decides pronunciation. English voices are the most polished.
See the [voice tips](USER-GUIDE.md#voices-for-fiction).

### How good is Kokoro?

Very good for its size (82 million parameters, small enough to run on a CPU): clear, natural-sounding
narration that stays consistent over a whole book. It reads rather than performs: no character voices
or acted emotion, and it sometimes mispronounces unusual names, invented words or heavy dialect. Use
**Preview voice** and a short story to judge for yourself.

### Can I use my own voice, or a different TTS engine?

Kokoro has no voice cloning, so not your own voice with the built-in engine. You can use any other
engine that offers an OpenAI-compatible `/v1/audio/speech` API by setting `TTS_ENGINE=openai` and
`TTS_OPENAI_URL`. See [DEPLOYMENT.md](DEPLOYMENT.md#using-an-external-tts-server-gpu).

### Does it use my GPU?

Not directly: the image has no CUDA support and runs Kokoro on the CPU. For GPU speed, run an
OpenAI-compatible server such as Kokoro-FastAPI on a machine with a GPU and point Lector at it with
`TTS_ENGINE=openai`.

### Can I change a book's voice after generating it?

Generate another audiobook with the new voice (or speed); both are kept side by side on the book
page. Listening progress is per audiobook, so the new one starts from the beginning. Delete the old
one if you no longer need it.

### What is the difference between narration speed and playback speed?

Narration speed (0.70x-1.50x, chosen when generating) is how fast the voice speaks in the audio files.
Playback speed (0.5x-3.0x, in the player) speeds up or slows down playback in your browser and can be
changed any time. For faster listening, use playback speed.

## Time, space and performance

### How long does generation take, and how big are the files?

About 16 characters of text per second of audio, so a 150,000-word novel is roughly 14-15 hours of
audio. On an Apple M4 that takes about 3 hours to generate at 5x realtime (`TTS_THREADS=8`). Audio
takes about 29 MB per hour (64 kbps MP3), so that novel needs about 440 MB. More examples are in the
[user guide](USER-GUIDE.md#how-long-generation-takes) and the
[disk planning table](DEPLOYMENT.md#disk-planning).

### Do I have to wait for the whole book?

No. Each chapter becomes playable as soon as it is finished. If you catch up with the narrator, the
player waits and continues automatically when the next chapter is ready.

### Can several people use it at once?

Yes. Each user has their own progress, bookmarks and settings, and any number of people can listen at
the same time. Generation runs one audiobook at a time, in the order requested; everyone shares the
finished audiobooks.

## Integration

### Does Lector sync my listening progress back to Kavita?

No. Lector reads from Kavita (books, covers, metadata) but never writes to it. Listening progress
lives only in Lector.

### Can I listen in Audiobookshelf or another app?

Yes. **Build M4B** on a finished audiobook, then **Download M4B**: a single file with chapters,
metadata and cover that any M4B-capable app (Audiobookshelf, Apple Books, BookPlayer, ...) can play.
You could also point another server at Lector's `audio/` folder, but the folders there are named by
numeric ids (`audio/<book id>/<audiobook id>/000.mp3`); the MP3s do carry title, album, artist and
track tags. M4B files are the cleaner option.

### Is there a mobile app?

Lector is a Progressive Web App: open it in your phone's browser and add it to the home screen. It then
runs in its own window with lock-screen controls. Full installation needs HTTPS. See
[installing as an app](USER-GUIDE.md#installing-lector-as-an-app-pwa).

### Can I listen offline?

Not in Lector: audio is always streamed from your server, and the app only caches its own interface.
For offline listening, download the M4B and play it in an audiobook app.

## Running the server

### Where does Lector keep its data?

Everything is in the host folder you set as `LECTOR_DATA_PATH` (`/data` in the container):
`lector.db` (users, books, audiobooks, progress, bookmarks, settings), `secret.key` (session key),
`audio/` (chapter files and M4B exports) and `cache/` (covers, voice previews, downloaded books). The
Kokoro model files are in `KOKORO_MODELS_PATH`. See [backups](DEPLOYMENT.md#backups-and-restore).

### How do I update?

```sh
git pull
docker compose up -d --build
```

Your data folder is kept. Back up `lector.db` first. See [DEPLOYMENT.md](DEPLOYMENT.md#updating).

### How do I delete audio to free space?

As an admin, open the book page and click **Delete** on the audiobook (this also removes everyone's
progress and bookmarks for it). M4B files can be deleted on their own from the data folder. See
[storage housekeeping](DEPLOYMENT.md#storage-housekeeping).

### How do I reset the admin password?

Another admin can set it on the admin page (Users, **Password**). If you are locked out completely,
use the database recipe in
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#forgotten-admin-password); it can also turn an existing user
into an admin.

### Who can generate audiobooks?

Admins always. Regular users can request them while the admin setting "Allow regular users to
request audiobooks" is on (the default). Only admins can pause, cancel, retry or delete them.

### Can I put Lector on the internet?

Not directly. Use a VPN to reach your home network, or an HTTPS reverse proxy with the
[security checklist](DEPLOYMENT.md#security-checklist) worked through.

### How do I report a bug?

Open an issue at <https://github.com/tahmidMahmud/lector/issues> with your Lector version, logs and
steps to reproduce. The checklist is in [TROUBLESHOOTING.md](TROUBLESHOOTING.md#reporting-a-bug).
