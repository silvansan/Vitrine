# Architecture

## Components

| Part | Where | Tech |
|---|---|---|
| Server | Home server, Portainer stack | Django 5 + Channels (daphne), Celery worker with ffmpeg/Pillow, Postgres, Redis, nginx |
| Agent | vMix PC, Windows service | Go, single exe; embeds the tablet app |
| Tray | vMix PC, user session | Go (`fyne.io/systray`), opens the companion page |
| Tablets | Venue LAN | Browser; pages served by the agent |

The agent connects **out** to the server over Tailscale (WebSocket + HTTP). The server never connects to the venue, so no port forwarding is needed. Tablets talk only to the agent on the LAN, so the show keeps running when the internet drops.

## Data model (`server/guestmedia/models.py`)

- **Organization**: staff accounts, people, locations and a library. `shares_with` lists organizations that may use its shared files; `share_all` shares every approved, non-private file, otherwise only files marked `shared`.
- **Location**: a venue or hall of an organization, with at most one **Agent** (`Location.agent`, one-to-one). Location = agent = one vMix PC.
- **Service** (shown as **Event**): a name and a period at a location: `starts_on` and `ends_on`, either may be empty (no start = already running, no end = open-ended, neither = ongoing). `ServiceQuerySet.overlapping()` / `not_over()` select by period; `sort_key()` puts events that are on today first. The model keeps the name `Service` because the agent protocol uses it.
- **Person**: belongs to an organization and has the token of their permanent link (`/g/<token>/`). **PersonAlias** keeps older tokens working (people merged when the organizations were introduced).
- **PersonDevice**: a device that entered the person's PIN (hash of its cookie token, browser label, IP, last seen). `Person.pin` (6 digits set by staff, readable so they can tell it) or `Person.pin_hash` (a 4–6 digit PIN the person chose, hashed like a password, with `pin_set_at` / `pin_set_from`); with neither, the next device that opens the link chooses one. `failed_pins` and `locked_until` (5 wrong tries lock it for 15 minutes). `guestmedia/pins.py` checks every guest request: page, API, thumbnails, files and tus uploads need either a remembered device (cookie `vitrine_p<person id>`, path `/g/`, HttpOnly, SameSite=Lax, ~10 years) or a staff session of the person's organization.
- **Guest**: a person taking part in one event (unique per event and person). It owns the person's set in that event; name, token and rights come from the person.
- **MediaAsset**: one file in the library (organization, uploader person, original + converted file, thumbnail, filmstrip, approval, private and shared flags, last used, soft delete). `MediaAsset.library_for(org)` is what an organization's people may pick: its own files plus those shared with it.
- Video sound (agent): with `video_audio` on, the engine sends `AudioAutoOff` + `AudioOn` to the slot it takes (after the transition starts), moves the sound with a crossfade seek, and sends `AudioOff` when a slot is paused after leaving the mix. With `audio_fade_ms` it uses `SetVolume 0` + `SetVolumeFade <volume>,<ms>` to fade in and `SetVolumeFade 0,<ms>` to fade out, then restores the input's volume (read from the vMix XML) once the slot is paused; the slot is paused after the longer of the picture transition and the sound fade. vMix's Audio Auto only reacts to the main output, which is why this is needed on Mix 2+.
- **SiteSettings** (one row, `/manage/settings/`): alert delivery (Telegram token, SMTP) and the backup target (kind, path, connection details as JSON, time, days to keep). `guestmedia/config.py` returns the effective values (Settings, else the stack environment) and turns the backup settings into an rclone configuration (passwords obscured) written to a temporary file for each run. `check_backup_due` and `check_sync_due` run every 5 minutes and start the job once a day when its time has come, so changed times apply without a restart.
- **BackupRun**: one nightly backup (`guestmedia/backup.py`: `pg_dump` + `rclone` to `VITRINE_BACKUP_TARGET`).
- Alerts (`guestmedia/alerts.py`, timers in `vitrine_server/celery.py`): `check_pending_uploads` and `check_agents` every 2 minutes, `check_sync` daily; `check_summary_due` every 5 minutes (morning summary, once a day via `SiteSettings.last_summary_day`); `MediaAsset.pending_notified` and `Agent.offline_notified` make each one fire once. One-off alerts are sent where they happen: `alerts.control_requested` (guest `request_access`), `alerts.conversion_failed` (`process_asset`), `alerts.first_upload` (`tus.finish_upload`), de-duplicated through the cache. Upload alerts in Telegram carry inline buttons with `callback_data` `a:<asset id>` / `r:<asset id>`; `telegram.poll_updates` (every 3 s on `light`) runs `process_updates`, which handles both `/start <code>` linking and button presses under a cache lock, and only accepts a press from the organization's own `notify_telegram_chat`. Control requests carry `ca:<set id>` / `cr:<set id>`: the server relays `{"action": "desk_control", "set_id", "on", "by"}` to the location's agent (`relay.send_command`; guests can't send this action, it isn't in `LIVE_ACTIONS`), which gives control until midnight or refuses the request (`Server.setControl`, the same as the back tablet's `/api/control`). The agent's heartbeat `sync.missing` lists the files it still has to download. Conversion runs on the `celery` queue (worker), timers and their jobs on `light` (scheduler).
- **MediaSet** / **SetItem**: a person's ordered list in an event, pointing at library files. The same file can be in many sets without being copied.
- **Marker**: named start point in a video.
- **Agent**: a vMix PC; token stored hashed; last heartbeat kept as JSON for the backend.
- **ScreenGrant**: a pairing code issued from the backend; revoking it unpairs the tablet.
- **Upload**: a resumable upload in progress.

## Uploads and conversion

The guest page uses tus-js-client against a small tus 1.0 server in Django (`tus.py`: creation, HEAD/PATCH, termination). Finished uploads move into `/data/media/originals/`, become a MediaAsset, are added to the guest's set, and a Celery task converts them (`processing.py`):

- Photos: EXIF rotation, HEIC support, 1920×1080 JPG, contained over a blurred, darkened cover of itself.
- Videos: one ffmpeg pass with the same blurred fill, `fps=VITRINE_VIDEO_FPS`, H.264 High CRF 20 at the chosen x264 preset (default `ultrafast`), GOP = 2 s, AAC 48 kHz stereo, `+faststart`. **Pass-through**: a video that is already H.264 1920×1080, 4:2:0, progressive, not rotated, at the project frame rate (or double it) is copied without re-encoding; only the audio is converted to AAC 48 kHz if needed. This takes seconds even for long videos. ffmpeg runs with `-progress pipe:1`; `progress.Reporter` writes `{stage, pct, eta}` to the cache (Redis) at most once a second (`vitrine:progress:<asset id>`, removed when done), and `GET /manage/progress?ids=…` serves it to `progress.js`, which draws a bar with the time left on Library tiles and swaps in the thumbnail when the file is ready.
- Every video gets a thumbnail and a sprite of up to 120 frames for the cue panel: one every `VITRINE_FILMSTRIP_INTERVAL` seconds, or further apart for long videos (a 3-hour video gets one every 90 s). Frames are taken with fast seeks, so long files aren't decoded in full.

The SHA-256 of the converted file goes into the manifest; the agent checks it after downloading.

Audio (MP3, M4A, WAV, FLAC…) becomes an MP4 too: the sound as AAC 48 kHz plus a still card (cover art, or a title card with the guest's name) at the project frame rate. The card is the thumbnail.

## Agent sync

`GET /api/agent/manifest` returns the active events **of the agent's location** whose period overlaps yesterday … `VITRINE_PRESYNC_DAYS` (14) days ahead, those on today first, each with `date` (start, for older agents), `ends_on`, `when` and `short_when` labels, plus `location` (id, name, organization). The agent downloads the files of all of them, so a person's media is on the vMix PC days before their event. An agent without a location gets no events. With the events come their sets (only approved, ready, not deleted files) and the files with markers, plus the server's Ed25519 public key and revoked pairing codes. The version an agent sees is `manifest_version × 1000 + day`, so it also refetches once a day when the event window moves. Any change bumps every agent's `manifest_version` and pings them over the WebSocket (`/ws/agent/`); agents also poll every 60 s and heartbeat every 20 s (`POST /api/agent/heartbeat`: LAN IP, vMix state, sync progress, settings, tablets, assets shown since the last heartbeat).

Downloads resume with HTTP Range (served by nginx via `X-Accel-Redirect`), are verified, then renamed into place. While anything is live, the download speed is capped (`live_throttle_kbps`). Files no longer in the manifest are deleted unless on air.

## vMix control (`agent/internal/engine`)

Two List inputs, A and B, take turns:

1. **Take**: load the file into the input that is off-air (`ListRemoveAll`, `ListAdd`, `SelectIndex 1`), for videos `SetPosition` + `Play`, then the transition (`Fade`/`Cut`/… with `Input=<key>`, `Duration`, `Mix`). The tile is *Sending…* until the agent reads from vMix's XML that the mix shows that input; then it is *Live*. The previous input is paused once it has faded out (unless something new was loaded into it in the meantime).
2. **Back to default**: transition the default input (found by key, then by name) onto the mix.
3. **Jump** (crossfade mode): load the same video into the other input at the new position and fade over 400 ms; in direct mode `SetPosition` on the live input.
4. **Pause / Play** on the live input.
5. The poller reads `/api` every 300 ms while a video is live (1 s otherwise): position and duration for the progress bars, end of video (→ back to default if enabled), and changes made directly in vMix (→ tablets clear the live frame). Positions of videos taken off mid-way are remembered for **Resume**.

6. **Audio with the default kept on screen**: the file is loaded into a third List input (`GM_AUDIO`), its automatic audio is turned off and audio on (so it is heard although it isn't on an output), and it plays; the projector mix isn't touched. Stop pauses it and turns its audio off. Pause, seek and markers apply to it when no video is live. With the option off, audio is taken like a video and its card shows on the projector.

The `Mix` parameter is `mix number + mix_param_offset` (default −1: Mix 2 → `Mix=1`); for the main output it is left out.

## Tablets and pairing

Pairing codes are `s1.<payload>.<Ed25519 signature>` from the server (backend or companion while online) or `a1.<payload>.<HMAC>` from the agent itself (companion while offline). The payload holds role, name, a nonce and a 15-minute expiry. The agent verifies the code locally, refuses reused or revoked nonces, creates a screen and sets a long-lived HttpOnly cookie (only its hash is stored).

Roles: **back** can control everything, edit pulpit screens and open the companion; **pulpit** sees its set and the controls its options allow. Screen options and themes live in the agent's `state.json`; the server gets a copy in the heartbeat for display.

Tablet ↔ agent: `GET /api/bootstrap`, WebSocket `/ws` (messages `state`, `catalog`, `screens`, `settings`, `command`), and `POST /api/take|clear|pause|play|seek|pulpit|screens/{id}|screens/{id}/command`.

Clips: a set is a list of `SetItem`s, each with optional `in_ms` / `out_ms` / `label` / `at_end` (`hold` or `fade`); the same video can be in a set several times, once per clip (there is no unique constraint per file any more; `MediaSet.add_asset` skips files already in the set). The guest page edits them with `POST /g/<token>/api/set/clip {mode: update|new|clear|restore, item_id, in_ms, out_ms, label, at_end}` and orders/removes by `item_ids` / `item_id`; the back tablet goes through the agent (`POST /api/clips`, back role) to `POST /api/agent/clips` (same rules, plus `mode: delete`; only sets of the agent's location). The manifest keeps `items` (each file once, for agents before 0.1.20) and adds `entries` (`{id, asset_id, clip, in_ms, out_ms, label, at_end}` in order). `/api/take` and the guest relay's `take` accept `entry_id`; the engine (`TakeClip`) starts at the mark in, keeps seeks inside the clip, and at the mark out (a timer armed by the 300 ms poll when less than 1.3 s is left) pauses or runs `Clear`. The live status carries `entry_id`, `pending_entry`, `clip`, `in_ms`, `out_ms`, `at_end`; positions of clips are kept per entry (`clip_positions`). Home-screen hand-over: `POST /api/handoff/new` (a paired tablet) returns a 6-digit code valid 10 minutes; `POST /api/handoff {code}` (no sign-in, 10 wrong codes a minute) moves that tablet's session to the calling browser: new session token, the old cookie stops working. Needed because iOS home-screen apps don't share Safari's cookies. `/static/manifest.webmanifest` (`display: fullscreen`) and `/static/icon.svg` make the tablet view installable.

Companion: `GET /api/companion/state`, `POST /api/companion/settings|pair|sync|test-default|screens/{id}/delete` (loopback or back tablet only).

Integrations (Bitfocus Companion module): API keys `vik_…` created at `GET|POST /api/companion/keys` and revoked at `POST /api/companion/keys/{id}/delete` (loopback only; the hash is kept in `state.json`). A key is accepted as `Authorization: Bearer` or `?key=` and acts as a back screen. `/api/take` also takes `{guest, file, from}` and `/api/pulpit` takes `{guest}`, both 1-based in the order the tablets show.

## Guest control through the server

A guest's tablet view (`/g/<token>/screen/`) is served by the server, so it works off the venue network. The agent's WebSocket to the server (`/ws/agent/`, which also carries sync notices) is used both ways:

- **Live state, agent → server**: every 0.5 s when it changes (at least every 15 s) the agent sends `{"type":"live", engine, controls:[set ids], requests:[set ids], service_id}`. The server keeps it in the cache (`vitrine:live:<agent id>`, 60 s), which is why the server needs `REDIS_URL` with more than one process.
- **Commands, server → agent**: `POST /g/<token>/api/live/<take|clear|pause|play|seek|stop_audio|request_access|cancel_request>` (PIN-protected like the rest of the guest link) checks that the agent is online, that its active event is the guest's, that the guest has control (except for request/cancel) and that the file is in their set; then it sends `{"type":"command", id, action, set_id, asset_id, start_ms, position_ms, by}` to the channel group `agent-<id>` and waits up to 4 s for `{"type":"command_result", id, ok, error}`.
- **The agent decides**: grants live in its `state.json` (`controls`: set id → end of day), requests only in memory (10 minutes). It checks the grant again, refuses files outside the set, and refuses clear/pause/seek unless what is live is the guest's own. The back tablet gives or takes control with `POST /api/control {set_id, on}` (back role only); requests and grants are part of the tablet `state` message, and each action is announced with a `notice` message.
- `GET /g/<token>/api/live` returns `{online, here, control, requested, live, audio, other_on_screen}`; `live`/`audio` are only filled in for the guest's own files.

Permanent tablet link: each location has a random `tablet_slug`; `/t/<slug>/` redirects to `http://<agent lan_ip>:<port>/screen` from the agent's last heartbeat, or answers 503 with a page that refreshes itself.

## Running the agent elsewhere

Everything vMix-related goes through `vmix_url`, so the agent can run on a second PC. vMix then plays files from a path it must be able to read (a share mapped to the same path as `media_dir`). Local disk on the vMix PC is safer for video, which is why the default is to run the agent on the vMix PC.
