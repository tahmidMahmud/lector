# Lector API contract

Lector is a self-hosted audiobook server for a Kavita library. JSON over HTTP, prefix `/api`.
Auth is an HttpOnly session cookie `lector_session` (set by login) **or** `Authorization: Bearer <token>`
**or** `?token=<token>` (for `<audio>`/`<img>` URLs that cannot send headers). Errors are
`{"detail": "message"}` with a 4xx/5xx status. All timestamps are ISO-8601 UTC strings.
Roles: `admin` (everything) and regular user. "Requester" below = admin, or any signed-in user
when the `allow_user_requests` setting is on.

## Objects

```jsonc
User          {id, username, is_admin, created_at, playback_rate, skip_back_seconds,
               skip_forward_seconds, preferred_voice, theme}            // theme: "dark"|"light"|"auto"
Voice         {id, name, language, gender}                               // e.g. {id:"af_heart", name:"Heart", language:"American English", gender:"female"}
Rendition     {id, book_id, voice, voice_name, speed, engine,
               status,            // "queued"|"running"|"paused"|"done"|"error"|"cancelled"
               progress,          // 0..1 of narrated chapters done
               done_chapters, total_chapters, total_duration /*sec*/, error, created_at,
               finished_at, has_m4b}
Chapter       {index, title, status /* "pending"|"running"|"done"|"error"|"skipped" */,
               duration /*sec*/, char_count, error}
Progress      {rendition_id, chapter_index, position /*sec in chapter*/, finished, playback_rate,
               updated_at, percent /*0..100 of whole rendition*/, remaining_seconds}
Bookmark      {id, rendition_id, chapter_index, position, note, created_at}
BookSummary   {id, title, series_title, volume_label, authors, summary, cover_url,
               word_count, file_format, is_gutenberg, missing,
               renditions: [Rendition], progress: Progress|null /* most recent for this user */}
BookDetail    BookSummary + {renditions: [Rendition + {chapters:[Chapter], progress: Progress|null,
               bookmarks:[Bookmark]}]}
ContinueItem  {book: BookSummary, rendition: Rendition, progress: Progress}
WorkerStatus  {rendition_id, book_title, chapter_index, chapter_title, chunk, chunks,
               realtime_factor, engine_ready, engine_message}
```

## Setup & auth
| Method | Path | Body | Returns | Notes |
|---|---|---|---|---|
| GET | /api/setup | | `{needs_setup, allow_registration, app:"Lector", version}` | `needs_setup` true until the first user exists |
| POST | /api/setup | `{username,password}` | `{user, token}` | creates the first **admin**; 409 once any user exists; sets cookie |
| POST | /api/auth/login | `{username,password}` | `{user, token}` | sets cookie; 401 on bad creds |
| POST | /api/auth/logout | | `{ok:true}` | clears cookie |
| POST | /api/auth/register | `{username,password}` | `{user, token}` | 403 unless `allow_registration` |
| GET | /api/auth/me | | User | 401 if signed out |
| PUT | /api/auth/me | any of `{playback_rate, skip_back_seconds, skip_forward_seconds, preferred_voice, theme}` | User | |
| PUT | /api/auth/password | `{current_password,new_password}` | `{ok:true}` | |

Username: 2–64 chars `[A-Za-z0-9_.-]`; password ≥ 6 chars.

## Library
| Method | Path | Returns | Notes |
|---|---|---|---|
| GET | /api/library | `{books:[BookSummary], continue:[ContinueItem]}` | books sorted by sort_title; `continue` = unfinished, most-recent first (max 12) |
| GET | /api/books/{book_id} | BookDetail | |
| GET | /api/books/{book_id}/cover | image/jpeg or png | 404 → UI shows placeholder; cacheable |
| POST | /api/books/{book_id}/renditions | body `{voice, speed?}` → Rendition | requester only. 200 with the existing rendition if (voice,speed) already exists; status set to `queued`; wakes the worker |
| GET | /api/voices | `{voices:[Voice], default_voice, default_speed}` | |
| GET | /api/voices/{voice}/preview | audio/mpeg | ~8 s sample sentence, generated on first request and cached |
| GET | /api/renditions/{rid} | Rendition + `{book: BookSummary, chapters, progress, bookmarks}` | |
| GET | /api/renditions/{rid}/chapters/{index}/audio | audio file | **must support HTTP Range** (206) so seeking works; `Accept-Ranges: bytes`; 404 unless chapter status is `done` |
| GET | /api/renditions/{rid}/chapters/{index}/timeline | `[{start,end,text,heading}]` | paragraph timings (seconds) for read-along; `[]` if missing |
| POST | /api/renditions/{rid}/pause \| resume \| cancel \| retry | Rendition | admin. retry re-queues `error`/`cancelled` (keeps finished chapters) |
| DELETE | /api/renditions/{rid} | `{ok:true}` | admin; deletes audio files, chapters, progress, bookmarks |
| POST | /api/renditions/{rid}/m4b | `{status:"building"|"ready"}` | requester; builds a chaptered .m4b in a background thread |
| GET | /api/renditions/{rid}/m4b | audio/mp4 download | 404 until built |

## Progress & bookmarks (per signed-in user)
| Method | Path | Body | Returns |
|---|---|---|---|
| GET | /api/progress/{rid} | | Progress (zeros if none) |
| PUT | /api/progress/{rid} | `{chapter_index, position, finished?, playback_rate?}` | Progress |
| GET | /api/renditions/{rid}/bookmarks | | `[Bookmark]` |
| POST | /api/renditions/{rid}/bookmarks | `{chapter_index, position, note?}` | Bookmark |
| PUT | /api/bookmarks/{id} | `{note}` | Bookmark |
| DELETE | /api/bookmarks/{id} | | `{ok:true}` |

The client saves progress every ~10 s while playing, on pause, on chapter change, and on page
hide (`navigator.sendBeacon` to `POST /api/progress/{rid}/beacon` with the same JSON body — this
POST alias exists because sendBeacon can only POST).

## Admin (admin only)
| Method | Path | Body | Returns |
|---|---|---|---|
| GET | /api/admin/status | | `{kavita:{url,configured,reachable,version}, engine:{name,ready,message}, worker:WorkerStatus, queue:[Rendition+{book_title}], last_sync:{started_at,finished_at,status,message,books_seen}|null, storage:{audio_bytes, free_bytes}, settings}` |
| POST | /api/admin/sync | | `{status:"started"}` or `{status:"busy"}`; runs in a thread |
| GET | /api/admin/users | | `[User]` |
| POST | /api/admin/users | `{username,password,is_admin}` | User |
| PUT | /api/admin/users/{id} | `{password?, is_admin?}` | User (cannot demote the last admin) |
| DELETE | /api/admin/users/{id} | | `{ok:true}` (cannot delete yourself) |
| GET | /api/admin/settings | | `{default_voice, default_speed, allow_user_requests, allow_registration}` |
| PUT | /api/admin/settings | same keys, any subset | settings |

Runtime settings live in the `settings` table and override the env defaults.

## Static
`GET /` and any non-`/api` path → `app/static/index.html` (SPA). `/static/*` serves `app/static/`.
`/manifest.webmanifest` and `/sw.js` are served from static too.
