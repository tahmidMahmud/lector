# Lector user guide

This guide walks through everything you can do in Lector's web app: signing in, browsing the
library, generating audiobooks, listening, and (for admins) running the server. For installing
Lector see [DEPLOYMENT.md](DEPLOYMENT.md); when something goes wrong see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md); quick answers are in [FAQ.md](FAQ.md).

A few words used throughout:

- **Book** - one EPUB file from your Kavita library. A multi-volume series in Kavita becomes
  several books, one per file.
- **Audiobook** (or *narration*) - one generated reading of a book with a particular **voice** and
  **narration speed**. A book can have several audiobooks, for example "Heart, 1.00x" and
  "George, 1.10x". All users share the same audiobooks.
- **Chapter** - Lector splits each book into chapters and narrates them one at a time, so you
  can start listening long before the whole book is done.

## Contents

- [First run: create the admin account](#first-run-create-the-admin-account)
- [Signing in](#signing-in)
- [The library page](#the-library-page)
- [The book page](#the-book-page)
- [The player](#the-player)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Settings page](#settings-page)
- [Installing Lector as an app (PWA)](#installing-lector-as-an-app-pwa)
- [Several users on one server](#several-users-on-one-server)
- [The admin page](#the-admin-page)
- [Tips](#tips)

## First run: create the admin account

The first time you open Lector (by default `http://<your-server>:5055`) you see **Set up
Lector**. This screen only appears while the server has no accounts.

1. Enter an **Admin username** (pre-filled with `admin`). Usernames are 2-64 characters:
   letters, digits, `_`, `.` and `-`.
2. Enter a **Password** (at least 6 characters, at most 72 bytes) and type it again in
   **Confirm password**.
3. Click **Create admin account**. You are signed in straight away and land on the library.

If the two passwords differ you see "Passwords do not match." and nothing is created.

Lector syncs with Kavita about 5 seconds after the server starts. If your library is empty
right after setup, wait a moment and reload the page, or press **Sync Kavita now** on the
[admin page](#the-admin-page).

## Signing in

The sign-in page ("Welcome back") asks for your username and password. Usernames are not
case-sensitive when you sign in.

- If an admin has turned on open registration, the page also shows **New here? Create an
  account**. New accounts made this way are regular (non-admin) users.
- Sessions last 30 days by default (the server's `SESSION_DAYS` setting). When a session expires
  you see "Your session expired - please sign in again." and, after signing in, you return to
  the page you were on.
- **Sign out** is the last item in the top bar. It saves your listening position first.

## The library page

The library is the home page (click **Library** or the Lector logo in the top bar).

### Continue listening

The shelf at the top lists audiobooks you have started but not finished, most recent first (up
to 12). Each item shows how far you are and how long is left, for example `42% · 6h 10m left`.
Click one to open the player where you left off.

The percentage and time left are counted over the chapters that have been generated so far, so
for an audiobook that is still being generated they grow as new chapters appear.

### Search and sort

- **Search** filters by title, author or series name as you type.
- **Sort** offers **Title** (a leading "The", "A" or "An" is ignored), **Author**, and
  **Recently played**.

Your search text and sort choice are remembered in this browser.

### Book cards and status chips

Each card shows the cover, title, author, a status chip, and a thin bar with your listening
progress once you have started.

| Chip | Meaning |
|---|---|
| **No audio** | Nobody has generated an audiobook for this book yet. |
| **Queued** | An audiobook is waiting for its turn in the narration queue. |
| **Generating N%** | An audiobook is being narrated right now (or is queued and partly done). |
| **Ready** | At least one audiobook is finished. |
| **Paused** | An admin paused the audiobook's generation. |
| **Error** | Generation stopped because of an error (an admin can retry it). |
| **Cancelled** | The only audiobook was cancelled. |

When a book has several audiobooks, the chip describes the most active one: anything being
generated wins, then "Ready", then "Paused", then "Error".

A red **Missing** label on the cover means Lector can no longer find the book's file - either
it was removed from Kavita or Lector cannot read it (see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#books-show-missing)). Audio and progress for missing books
are kept.

While anything is queued or generating, the library refreshes itself every 15 seconds.

### Multi-volume series

When several books belong to the same Kavita series, they are grouped under a series heading
with a "N volumes" chip and shown on a horizontal shelf. Each volume card carries its volume
label (for example "Volume 2") on the cover.

### Covers

Covers come from Kavita and are copied to Lector during sync. A book without a cover gets a
coloured placeholder showing its title and author.

## The book page

Click any book card to open its page. At the top you see the cover, series and volume (if any),
title, authors and a few facts:

- **Project Gutenberg text, boilerplate trimmed** - this book was identified as a Project
  Gutenberg ebook. Lector narrates only the text between Gutenberg's
  `*** START OF THE PROJECT GUTENBERG EBOOK ... ***` and `*** END OF ... ***` markers, so the
  legal header and the long license at the end are not read aloud.
- **N words** - the word count reported by Kavita (useful for estimating length; see
  [Tips](#tips)).
- The file format (for example **EPUB**). Only EPUB books can be narrated.
- **Missing from Kavita** - see the Missing label above.
- The book's summary, with **Show more** for long ones.

### Audiobooks

The **Audiobooks** section has one card per narration of this book. Each card shows:

- The voice name and narration speed, for example `Heart · 1.00x`, and a status chip
  (Queued, Generating N%, Paused, Ready, Error, Cancelled).
- A line such as `12/31 chapters · 3:05:12 · kokoro · 40% listened`: chapters generated, total
  audio length so far, the TTS engine used, and your own listening progress.
- While generating, a live line like
  `Narrating Chapter 13 · segment 18/96 · about 2 h 5 min left · 4.9× realtime` and a progress bar.
  "Segment" counts the short pieces (a sentence or two each) the chapter is cut into. The time
  left is an estimate based on the remaining text and the measured speed.
- Any error message.
- **Play** (or **Resume** if you have already started) as soon as at least one chapter is ready.
- **Chapters (N)** - expand it to see every chapter with its status and length:

  | Icon / label | Meaning |
  |---|---|
  | check mark, length | Done and playable |
  | spinner, "generating…" | Being narrated now |
  | empty circle, "pending" | Waiting to be narrated |
  | crossed circle, "error" | This chapter failed |
  | minus circle, "skipped" | Nothing to read aloud (for example a table of contents) |

  The play icon next to a finished chapter opens the player at the start of that chapter.

### Generating (or requesting) an audiobook

Below the audiobooks is the **Generate audiobook** panel (called **Request audiobook** for
regular users).

1. **Voice** - voices are grouped by language (American English, British English, Spanish,
   French, Hindi, Italian, Japanese, Brazilian Portuguese, Mandarin Chinese) and labelled with
   their gender, for example "Heart (female)". The list starts on your preferred voice from
   [Settings](#settings-page), or the server default. Pick a voice in the **book's language**:
   the voice decides how words are pronounced.
2. **Preview voice** plays a short sample sentence (about 8 seconds) in the selected voice. The
   first preview of each voice takes a few seconds to generate; after that it is cached. Click
   **Stop preview** to stop it. The preview is played back at the speed you have on the slider,
   which gives you a rough idea of that speed. Starting a preview pauses the player.
3. **Speed** - the narration speed, from 0.70x to 1.50x in steps of 0.05. This is the pace the
   voice actually speaks at, baked into the audio file. It is different from the
   [playback speed](#playback-speed), which you can change at any time while listening. If you
   just want to listen faster, leave the narration speed near 1.00x and raise the playback speed
   instead.
4. Click **Generate audiobook** (or **Request audiobook**).

What happens next:

- You see "Queued for narration." (or "Request sent - narration is queued.") and a new card
  appears with the status **Queued**.
- The server narrates one audiobook at a time, oldest request first. When yours starts, the
  status changes to **Generating** and the live line appears. The page updates every 5 seconds
  while narrating.
- Each chapter becomes playable as soon as it is encoded. You can press **Play** right away and
  listen while the rest is generated.
- When every chapter is done the status becomes **Ready**.
- If an audiobook with the same voice and speed already exists, no duplicate is made: you are
  pointed at the existing one (and a cancelled one is put back in the queue).
- Regular users can only request audiobooks while the admin setting "Allow regular users to
  request audiobooks" is on. Otherwise the button answers with "Only admins can request
  narrations - Audiobook requests are turned off on this server. Ask an admin."

### Managing audiobooks (admins)

Admins see extra buttons on each audiobook card (the same actions are on the admin page's
queue):

| Button | Shown when | What it does |
|---|---|---|
| **Pause** | Queued or generating | Stops generation. It takes effect within a few segments. The chapter that was in progress is discarded and starts again from its beginning on resume; finished chapters are kept and stay playable. |
| **Resume** | Paused | Puts the audiobook back in the queue. |
| **Cancel** | Queued, generating or paused | Stops generation for good and marks it **Cancelled**. Finished chapters are kept and stay playable. |
| **Retry** | Error or cancelled | Re-queues it. Finished chapters are kept; failed chapters are tried again. |
| **Delete** | Always | After a confirmation, deletes the audio files, the chapters, the M4B file and **every user's** progress and bookmarks for this audiobook. Cannot be undone. |

If the server restarts in the middle of a narration, the audiobook goes back to the queue on
start-up and continues with the chapter that was interrupted. Finished chapters are never
redone.

### Build M4B / Download M4B

When an audiobook is **Ready**, its card shows **Build M4B**. Click it to have the server join
all chapters into a single `.m4b` audiobook file in the background ("Building M4B in the
background…"). When it is done the button turns into **Download M4B**, and the file downloads
as `<Book title>.m4b`.

The M4B contains:

- every finished chapter, in order, as AAC audio (at the server's bitrate, 64 kbps by default),
- a chapter marker for each chapter, named like the chapters in Lector,
- the book title, author and the "Audiobook" genre as metadata,
- the cover image, if Lector has one.

Use it in any audiobook app that understands M4B (Apple Books, Audiobookshelf, BookPlayer,
Smart AudioBook Player and so on). Building an M4B needs about as much disk space again as the
audiobook itself. Anyone can download an M4B once it exists; building one needs the same
permission as requesting an audiobook.

## The player

Click **Play**/**Resume** on a book page, a chapter's play icon, or an item on the Continue
listening shelf. On a wide screen the player shows the controls on the left and the
transcript/bookmarks on the right; on a phone they are stacked.

### Resuming

The player opens where you left off in that audiobook: same chapter, same position. Opening it
from a chapter's play icon on the book page starts that chapter instead, and starts playing (from
your saved position if it is the chapter you were in, otherwise from its beginning).
If you had finished the audiobook, it starts again from the first chapter. If the chapter you
stopped in has not been generated yet but a later one has, the player jumps to the first ready
chapter.

Your position is saved to the server every 10 seconds while playing, whenever you pause, seek or
change chapter, and when you close or hide the tab. Because it lives on the server, you can stop
on your phone and continue on your laptop.

### Controls

From top to bottom:

- The **back link** (book title) returns to the book page. The audio keeps playing in the
  [mini-player](#the-mini-player).
- Cover, book title, current **chapter title** and authors.
- The **scrubber** seeks within the current chapter. While you drag, the elapsed time follows
  your finger; the jump happens when you let go. On the left is the elapsed time, on the right
  the time left in the chapter (adjusted for your playback speed).
- The control row:

  | Button | Action |
  |---|---|
  | Previous chapter | If you are more than 3 seconds into the chapter, jumps back to its start. Otherwise goes to the previous ready chapter. |
  | Skip back (shows the seconds) | Jumps back by your **Skip back** setting (15 s by default). |
  | Play / Pause | Starts or pauses playback. |
  | Skip forward (shows the seconds) | Jumps ahead by your **Skip forward** setting (30 s by default). |
  | Next chapter | Goes to the next chapter, skipping chapters marked skipped or error. Disabled on the last chapter. |

  The skip seconds come from your [Settings](#settings-page) and are shown on the buttons.
- **Chapter N of M** with your progress through the whole book, for example
  `38% · 9h 12m left`. Time left includes chapters not generated yet (estimated from their
  length in characters) and is adjusted for your playback speed.
- The tool row: **Speed**, **Sleep**, **Bookmark**, **Chapters** and **Mute**. A tool is
  highlighted while it is active (speed other than 1.0x, a sleep timer running, muted).

### Chapter drawer

**Chapters** opens a list of all chapters with their status and length; the current one is
highlighted. Tap a ready chapter to play it from the start (tapping the current chapter
toggles play/pause). Chapters that are not ready are greyed out. The list updates as new
chapters finish. Close it with the X, by tapping outside it, or with Escape.

### Playback speed

**Speed** opens the playback speed sheet: a big readout, **-** and **+** buttons (0.1x steps),
a slider from 0.5x to 3.0x, and presets (0.75x, 1x, 1.25x, 1.5x, 1.75x, 2x, 2.5x, 3x). You can
also press `[` and `]`.

The speed you choose is saved twice: for this audiobook (in your progress), and as your account's
default speed. When you open an audiobook, the player uses that audiobook's saved speed, or your
default if you have never changed it there. Playback speed is per user; it never changes the
audio files.

### Sleep timer

**Sleep** offers **Off**, **15 minutes**, **30 minutes**, **45 minutes**, **60 minutes** and
**End of chapter**.

- With a minute timer, the Sleep button shows the remaining time (for example `12:40`). The
  countdown only runs while audio is playing; pausing freezes it. The volume fades out over the
  last 10 seconds, then playback pauses ("Sleep timer: playback paused.").
- With **End of chapter**, the button shows "End ch.". The volume fades over the last 10
  seconds of the chapter and playback stops at its end ("Sleep timer: stopped at end of
  chapter."). The next chapter is not started. To carry on, use **Next chapter**; pressing Play
  straight away plays the same chapter again from its beginning.
- Choosing **Off**, or loading a different audiobook, cancels the timer.

### Bookmarks

Press **Bookmark** in the tool row, press `B`, or use **Add at current time** in the Bookmarks
tab. A dialog shows the chapter and time and lets you type an optional note. Press Enter or
**Add bookmark** to save (Shift+Enter adds a new line to the note).

The **Bookmarks** tab (next to Transcript) lists your bookmarks for this audiobook in book
order, each with its chapter, time and note (or when you made it, if there is no note). Click
one to jump there and start playing. The pencil icon edits the note; the bin icon deletes the
bookmark after confirming. Bookmarks are private to your account. You cannot add a bookmark
while the player is waiting for a chapter to be generated.

### Transcript (read-along)

The **Transcript** tab shows the text of the current chapter as it was spoken, one paragraph
at a time, with the paragraph being read highlighted. Chapter headings are styled differently.

- Click or tap a paragraph (or focus it and press Enter or Space) to jump to the start of it.
  Playback starts if it was paused.
- **Auto-scroll** keeps the current paragraph in view. If you scroll the transcript yourself,
  auto-scrolling pauses for a few seconds so you can read around. Untick the box to turn it off.
- The text is the cleaned-up version that was sent to the voice, so it can differ slightly from
  the book: footnote markers removed, "&" written as "and", Roman chapter numbers as digits, and
  in English books accents removed (for example "Dantès" appears as "Dantes").
- While a chapter is still generating you see "The transcript appears once this chapter has
  been narrated."

Lector remembers which tab you had open and your auto-scroll choice in this browser.

### End of a chapter and end of the book

When a chapter ends, the next one starts automatically (chapters marked skipped or error are
passed over).

If the next chapter is not generated yet, you see "Next chapter is still generating - it will
start automatically when ready." The player shows "This chapter is still generating - checking
every 15 s…", disables play, skip and the scrubber, and checks the server every 15 seconds. As
soon as the chapter is ready you get "Next chapter is ready." and playback continues. The same
waiting happens if you move to a chapter that is not ready with **Next chapter**.

At the end of the last chapter you see "Finished "*title*". Nice!", the audiobook is marked as
finished and leaves your Continue listening shelf. Opening it again starts from the beginning.

### The mini-player

When you leave the player while an audiobook is loaded, a mini-player sits at the bottom of
every page. It shows the cover, title and chapter ("Generating…" while waiting for a chapter), a
thin bar with your progress through the book, a skip-back button and play/pause. Tap the title
to return to the full player. The keyboard shortcuts keep working on other pages too.

### Lock screen, notification and media keys

Lector tells your browser what is playing, so phones show it on the lock screen and in the
notification shade, and desktop browsers respond to media keys and headset buttons:

- Title: the chapter title. Artist: the authors. Album: the book title. Artwork: the cover.
- Play, pause and stop (stop pauses).
- Seek backward / forward use your skip settings, unless the system asks for a specific
  number of seconds.
- Previous track / next track move between chapters.
- The seek bar, where the system shows one, seeks within the chapter.

How much of this appears, and whether audio keeps playing with the screen off, depends on your
phone and browser.

### Mute

**Mute** (or `M`) silences the audio without pausing it. There is no volume slider; use your
device's volume.

## Keyboard shortcuts

Shortcuts work whenever an audiobook is loaded (in the player or the mini-player), except while
you are typing in a field or a dialog is open.

| Key | Action |
|---|---|
| `Space` or `K` | Play / pause (when a button or link has focus, Space presses that instead) |
| `←` | Skip back by your Skip back setting |
| `→` | Skip forward by your Skip forward setting |
| `Shift` + `←` | Previous chapter (or restart the current one if more than 3 s in) |
| `Shift` + `→` | Next chapter |
| `[` | Playback speed down 0.1x |
| `]` | Playback speed up 0.1x |
| `B` | Add a bookmark at the current time |
| `M` | Mute / unmute |
| `Escape` | Close the chapters, speed or sleep sheet, or a dialog |

While a sheet (chapters, speed, sleep) is open, only Escape works. Shortcuts with Ctrl, Cmd or
Alt held are left to the browser.

## Settings page

Open **Settings** in the top bar. It shows who you are signed in as.

**Playback**

| Field | What it does |
|---|---|
| **Skip back** | Seconds the skip-back button, `←` and the lock-screen "seek backward" jump: 5, 10, 15, 20, 30, 45, 60 or 90 (default 15). |
| **Skip forward** | Same for skipping ahead (default 30). |
| **Default speed** | Your default playback speed, 0.5x to 3.0x. Used for audiobooks where you have not picked a speed yet. Changing the speed in the player also updates this. |
| **Preferred narration voice** | The voice pre-selected when you generate or request an audiobook. "Server default" uses the admin's default voice. |

**Appearance** - **Dark**, **Light** or **Match system**. Clicking one previews it immediately.

Click **Save settings** to keep your changes. They are stored with your account, so they follow
you to other devices.

**Change password** - enter your **Current password**, a **New password** (at least 6
characters) and **Confirm new password**, then **Update password**. Mismatched entries show "New
passwords do not match."; a wrong current password shows "Current password is incorrect".

**Keyboard shortcuts** - a short reminder of the [shortcuts](#keyboard-shortcuts).

## Installing Lector as an app (PWA)

Lector is a Progressive Web App: you can add it to your home screen or desktop, and it then
opens in its own window without browser bars.

- **iPhone / iPad (Safari):** open Lector, tap Share, then **Add to Home Screen**.
- **Android (Chrome):** open Lector, open the menu, then **Install app** or **Add to Home
  screen**.
- **Desktop (Chrome, Edge):** click the install icon at the right of the address bar, or use the
  menu's **Install Lector** / **Apps** entry.

Limits:

- **Full installation needs HTTPS.** Browsers only enable Lector's service worker on `https://`
  addresses (and on `localhost`). On a plain `http://192.168.x.x:5055` address you may only get
  a home-screen shortcut. See [DEPLOYMENT.md](DEPLOYMENT.md#reverse-proxy-and-https) for
  putting Lector behind HTTPS.
- **No offline listening.** The service worker only caches the app itself (pages, script,
  styles, icon), never audio, covers or data. Without a connection to your server the app shows
  "Can't reach the Lector server". To listen offline, download the M4B and use an audiobook app.
- Updates arrive automatically: the app always fetches the newest version from the server when
  it is reachable. If an old screen lingers after an upgrade, see
  [TROUBLESHOOTING.md](TROUBLESHOOTING.md#old-interface-after-an-upgrade).

## Several users on one server

| Shared by everyone | Private to each user |
|---|---|
| The library (books, covers, summaries) | Listening position and "finished" state per audiobook |
| Audiobooks and their audio files | Bookmarks and notes |
| M4B files | Continue listening shelf |
| The narration queue | Playback speed (default and per audiobook) |
| Server settings (default voice and speed, request and registration switches) | Skip settings, preferred voice, theme |

Remembered only in the browser you use: the library search and sort, the player's open tab and
the auto-scroll checkbox.

Any number of people can listen at the same time; the server just streams the files. Generation,
on the other hand, runs one audiobook at a time for everyone.

Regular users can browse, listen, bookmark, request audiobooks (if allowed), build M4B files (if
allowed) and download them. Only admins can pause, resume, cancel, retry or delete audiobooks,
see the admin page, and manage users and server settings. Remember that deleting an audiobook
removes everyone's progress and bookmarks for it.

## The admin page

Admins have **Admin** in the top bar. The page refreshes its status and queue every 5 seconds
while it is visible.

### Status cards

| Card | Shows |
|---|---|
| **Kavita** | **Reachable**, **Unreachable** or **Not configured**, Kavita's version, and the URL Lector uses. "Reachable" only means Kavita answers; the version appears only when Lector can also log in. |
| **TTS engine** | The engine name (`kokoro` or `openai`) with **ready** or **not ready**, and the engine's message (an error explains what is wrong). |
| **Worker** | The book being narrated, the current chapter, a bar and "Chunk N of M" for that chapter, and the speed as a realtime factor (for example `4.85× realtime` means one minute of work produces 4.85 minutes of audio). Shows **Idle** when nothing is being narrated. |
| **Storage** | Total size of all generated audio (including M4B files) and the free space on that disk. |

### Kavita library sync

Shows when the last sync ran, its status (`ok`, `running` or `error`), how many books were seen,
and its message, for example
`8 book file(s) in 7 series across 1 library(ies); 0 new, 0 missing`. Errors for individual
libraries or series are appended to the message.

**Sync Kavita now** starts a sync in the background ("Sync started.", or "A sync is already
running."). Automatic syncs also run every 60 minutes by default. Reload the library page
afterwards to see new books.

### Narration queue

Lists audiobooks that are generating, queued, paused or failed, in that order, with the book,
voice and speed, status, a progress bar with chapters done and an estimated time left, and icon
buttons for pause, resume, cancel, retry and delete (they work like the
[buttons on the book page](#managing-audiobooks-admins)). Finished and cancelled audiobooks are
not listed here; manage them from their book page.

### Users

A table of all accounts with their admin checkbox and creation date.

- Tick or untick **Admin** to change someone's role ("Role updated."). The last admin cannot be
  demoted.
- **Password** sets a new password for that user (at least 6 characters). Their existing
  sessions stay signed in.
- The bin icon deletes a user after confirming, together with their progress and bookmarks. You
  cannot delete yourself. Audiobooks they requested stay.
- **Add user**: enter a username and password, tick **Admin** if needed, and click **Add user**.

### Server settings

| Setting | Effect |
|---|---|
| **Default voice** | The voice pre-selected for users who have no preferred voice. |
| **Default narration speed** | The narration speed pre-selected in the generate panel (0.70x to 1.50x). |
| **Allow regular users to request audiobooks** | When off, only admins can queue audiobooks and build M4B files. |
| **Allow new users to register on the sign-in page** | Shows "Create an account" on the sign-in page. |

Click **Save server settings**. These values are stored in Lector's database and take
precedence over the matching environment variables (`DEFAULT_VOICE`, `DEFAULT_SPEED`,
`ALLOW_USER_REQUESTS`, `ALLOW_REGISTRATION`): once you have saved this form, changing those
variables has no effect.

## Tips

### Voices for fiction

Kokoro's American and British English voices are its strongest. Good starting points for novels:

- **Heart** (American, female) - the default, warm and clear; the top-graded voice in
  Kokoro's own voice list.
- **Bella** (American, female) - also highly graded.
- **Emma** (British, female).
- **Michael** and **Fenrir** (American, male).
- **George** and **Fable** (British, male).

Use **Preview voice** before committing to a whole book. For a book in another language, pick a
voice of that language (French, Spanish, Italian and so on). See [FAQ.md](FAQ.md) for more on
languages.

### Speed

Narration at 1.00x sounds most natural. Lower narration speeds (0.90-0.95x) can help with dense
prose; higher ones start to sound rushed. To listen faster, raise the playback speed in the
player instead: it costs nothing and works on every audiobook.

### Start with something short

Your first generation is a good test of the whole setup. Use the search box to find a short
story or novella and check its word count on the book page before generating. You will hear the
first chapter within minutes and can check voice, chapter splitting and speed before queueing a
long novel.

### How long generation takes

Kokoro speaks roughly 16 characters per second, so a book's audio length follows its word count.
Generation speed depends on your CPU and is shown on the admin page as the realtime factor. The
table below uses two measured figures from an Apple M4 running Lector in Docker: about **5x
realtime** with `TTS_THREADS=8`, and about **2.2x** with the default thread setting.

| Book | Words | Audio | Generation at 5x | Generation at 2.2x | Size (64 kbps MP3) |
|---|---|---|---|---|---|
| Short story | 7,500 | ~45 min | ~10 min | ~20 min | ~22 MB |
| Novella | 30,000 | ~3 h | ~35 min | ~1 h 20 min | ~90 MB |
| Novel | 90,000 | ~9 h | ~1 h 50 min | ~4 h | ~260 MB |
| Long novel | 150,000 | ~15 h | ~3 h | ~7 h | ~440 MB |
| *The Count of Monte Cristo* | ~464,000 | ~47 h | ~9-10 h | ~21 h | ~1.35 GB |

Building an M4B afterwards is much quicker than narration and roughly doubles the disk space
the book uses. Remember you can start listening as soon as the first chapter is done; generation at
anything above 1x stays ahead of you.
