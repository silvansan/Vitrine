# Using Vitrine

## Before the service (backend, `/manage/`)

1. **Services → New service** (name, date). Keep it **active** so the agent receives it. Tick **auto-approve** only for services where you trust every guest.
2. **Add a guest** for each speaker or group. Each gets a link and a QR code: **Copy link**, or print/show the QR. **New link** makes the old one stop working. **Open as guest** lets you fill a set for someone.
3. Guests open the link on their phone:
   - **Upload**: photos and videos from the gallery. Uploads resume after Wi-Fi dropouts while the page stays open.
   - **Library**: pick files others uploaded ("This service", "Earlier services", "My uploads") and **Add to my set**.
   - **My set**: order with the arrows; **Options** to remove a file, mark own uploads private, and add markers to videos (name + time, e.g. "Baptism · 1:12"). For a video, **Options** also shows a player: pause where you want a start point and press **Add marker here**. Markers show up as jump buttons on the tablets and in the cue panel.

Tip: videos that are already H.264 1920×1080 at your project frame rate (`VITRINE_VIDEO_FPS`) are ready in seconds, because they are copied instead of converted.
4. **Waiting for approval** on the service page: approve or reject. Rejected files never reach the vMix PC. Converted files appear on the tablets as soon as the agent has downloaded them.

## Library housekeeping (`/manage/library/`)

Filter by name, guest, type, approval, or **not used for 30/90/365 days** ("used" = shown live from a tablet). Select files and **Move to trash**: if a file is still in a set of an active service you'll be warned first. Trash keeps files for 30 days (**Restore** or **Delete for good**). **Make private** hides a file from other guests' libraries; **Process again** re-runs conversion.

Each file's page has a preview, the original download, the sets it is in, and markers.

## Pairing tablets

Either in the backend (**Agents & tablets → Show pairing QR**, choose Pulpit or Back) or in the tray companion (**Pair a tablet…**). Scan the QR with the tablet's camera while it is on the venue network. A code works once, for 15 minutes. Add the page to the home screen; on iPad use **Guided Access**, on Android **screen pinning**, so the pulpit can't leave the page.

Unpair from the companion, or **Unpair** next to the code in the backend (takes effect at the agent's next sync).

## Back tablet

- **Guest chips** filter the tiles; the small screen icon marks the guest the pulpit shows.
- **Tap a tile**: it turns amber (**SENDING…**) and then red (**LIVE · MIX 2**) once vMix confirms. **Tap again** or **Back to default** returns the projector to the default input.
- **Videos**: a playback bar appears: **From start**, **−10 s**, pause/play, **+10 s**, marker buttons, time left. Jumps use the second input and a short crossfade, so the audience doesn't see the picture skip.
- **Resume 0:47** appears on a video that was taken off mid-way. A normal tap always starts from 0:00.
- **Audio files** (MP3 and others) have a note icon or their cover. By default a tap plays only the sound while the projector keeps showing the default input; the tile shows **PLAYING · AUDIO**, the playback bar gets **Stop audio**, and it stops by itself at the end. A picture can be put on the projector while audio plays. To show the audio's cover/title card on the projector instead, untick *Audio files: keep the default input on the projector* in the settings page on the vMix PC.
- **⋯** (next to the name, under each video tile) opens the **cue panel**: drag along the bar, tap a filmstrip frame or a marker, nudge ±10/30 s, then **Start here**. Nothing changes on the projector until you press it.
- **Add marker here**: in the cue panel (at the cued point) or on the playback bar while a video is live (**Marker**, at the current position). Give it a name or leave it empty ("Marker 1:12"). Markers appear on every tablet at once and are saved on the server, also when added offline (they are sent when the connection is back). The back tablet can remove a marker with the × next to it.
- **Send Maria to pulpit**: the pulpit switches to Maria's set immediately.
- **Tablets** button (top right): for each pulpit tablet choose the set, **Jump to page 1**, **Mirror my page**, what is visible (Clear button, title, switching sets, connection status, basic video controls, free seeking) and its theme.
- Sun/moon button: dark or light theme for this tablet.
- Swipe left/right on the tiles to change pages.

## Phones

The tablet pages also work on a phone (upright or sideways): the bars get smaller and the tiles scroll instead of shrinking. Use Chrome, Samsung Internet or Safari; Brave's HTTPS upgrade blocks the local address unless you turn its Shields down for it.

## Pulpit tablet

Shows the set chosen from the back, 6 numbered tiles per page, the status ("Screen shows the banner" / "A picture is on screen") and, if allowed, **Clear screen**, the video bar (From start, Pause, markers) and free seeking (cue panel, ±10 s, Add marker here). Free seeking is on by default; untick it in the back tablet's Tablets panel to keep the pulpit simpler. When a speaker asks for a picture they can't find, switch their set or page from the back; the pulpit follows within a second.

## When things go wrong

- **"This file is not on the vMix PC yet"**: it is still downloading; the tile shows *Downloading…*.
- **Red message "vMix didn't switch Mix 2"**: check the projector mix and `mix_param_offset` (docs/agent.md).
- **"No default input set"**: choose it in the companion.
- **Server offline**: tablets keep working with the files already downloaded; new uploads arrive when the connection is back.
- **Someone switches the mix in vMix directly**: the tablets clear the red frame and follow vMix.
