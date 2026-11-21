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
- **Service** (shown as **Event**): a name and date at a location. The model keeps the name `Service` because the agent protocol uses it.
- **Person**: belongs to an organization and has the token of their permanent link (`/g/<token>/`). **PersonAlias** keeps older tokens working (people merged when the organizations were introduced).
- **PersonDevice**: a device that entered the person's PIN (hash of its cookie token, browser label, IP, last seen). `Person.pin` (6 digits, readable by staff so they can tell it), `failed_pins` and `locked_until` (5 wrong tries lock it for 15 minutes). `guestmedia/pins.py` checks every guest request: page, API, thumbnails, files and tus uploads need either a remembered device (cookie `vitrine_p<person id>`, path `/g/`, HttpOnly, SameSite=Lax, ~10 years) or a staff session of the person's organization.
- **Guest**: a person taking part in one event (unique per event and person). It owns the person's set in that event; name, token and rights come from the person.
- **MediaAsset**: one file in the library (organization, uploader person, original + converted file, thumbnail, filmstrip, approval, private and shared flags, last used, soft delete). `MediaAsset.library_for(org)` is what an organization's people may pick: its own files plus those shared with it.
- **MediaSet** / **SetItem**: a person's ordered list in an event, pointing at library files. The same file can be in many sets without being copied.
- **Marker**: named start point in a video.
- **Agent**: a vMix PC; token stored hashed; last heartbeat kept as JSON for the backend.
- **ScreenGrant**: a pairing code issued from the backend; revoking it unpairs the tablet.
- **Upload**: a resumable upload in progress.

## Uploads and conversion

The guest page uses tus-js-client against a small tus 1.0 server in Django (`tus.py`: creation, HEAD/PATCH, termination). Finished uploads move into `/data/media/originals/`, become a MediaAsset, are added to the guest's set, and a Celery task converts them (`processing.py`):

- Photos: EXIF rotation, HEIC support, 1920×1080 JPG, contained over a blurred, darkened cover of itself.
- Videos: one ffmpeg pass with the same blurred fill, `fps=VITRINE_VIDEO_FPS`, H.264 High CRF 20, GOP = 2 s, AAC 48 kHz stereo, `+faststart`. **Pass-through**: a video that is already H.264 1920×1080, 4:2:0, progressive, not rotated, at the project frame rate (or double it) is copied without re-encoding; only the audio is converted to AAC 48 kHz if needed. This takes seconds even for long videos.
- Every video gets a thumbnail and a sprite of up to 120 frames for the cue panel: one every `VITRINE_FILMSTRIP_INTERVAL` seconds, or further apart for long videos (a 3-hour video gets one every 90 s). Frames are taken with fast seeks, so long files aren't decoded in full.

The SHA-256 of the converted file goes into the manifest; the agent checks it after downloading.

Audio (MP3, M4A, WAV, FLAC…) becomes an MP4 too: the sound as AAC 48 kHz plus a still card (cover art, or a title card with the guest's name) at the project frame rate. The card is the thumbnail.

## Agent sync

`GET /api/agent/manifest` returns the active events **of the agent's location** from yesterday up to `VITRINE_PRESYNC_DAYS` (14) ahead, today's or the next one first, plus `location` (id, name, organization). The agent downloads the files of all of them, so a person's media is on the vMix PC days before their event. An agent without a location gets no events. With the events come their sets (only approved, ready, not deleted files) and the files with markers, plus the server's Ed25519 public key and revoked pairing codes. The version an agent sees is `manifest_version × 1000 + day`, so it also refetches once a day when the event window moves. Any change bumps every agent's `manifest_version` and pings them over the WebSocket (`/ws/agent/`); agents also poll every 60 s and heartbeat every 20 s (`POST /api/agent/heartbeat`: LAN IP, vMix state, sync progress, settings, tablets, assets shown since the last heartbeat).

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

Tablet ↔ agent: `GET /api/bootstrap`, WebSocket `/ws` (messages `state`, `catalog`, `screens`, `settings`, `command`), and `POST /api/take|clear|pause|play|seek|pulpit|screens/{id}|screens/{id}/command`. Companion: `GET /api/companion/state`, `POST /api/companion/settings|pair|sync|test-default|screens/{id}/delete` (loopback or back tablet only).

Integrations (Bitfocus Companion module): API keys `vik_…` created at `GET|POST /api/companion/keys` and revoked at `POST /api/companion/keys/{id}/delete` (loopback only; the hash is kept in `state.json`). A key is accepted as `Authorization: Bearer` or `?key=` and acts as a back screen. `/api/take` also takes `{guest, file, from}` and `/api/pulpit` takes `{guest}`, both 1-based in the order the tablets show.

## Running the agent elsewhere

Everything vMix-related goes through `vmix_url`, so the agent can run on a second PC. vMix then plays files from a path it must be able to read (a share mapped to the same path as `media_dir`). Local disk on the vMix PC is safer for video, which is why the default is to run the agent on the vMix PC.
