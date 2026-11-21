# Using Vitrine

## Full screen

The **full-screen button** (four corners, top right on the back tablet, next to the status on the pulpit) hides the browser's address bar and tabs; press it again or swipe from the edge to leave. It works on Android tablets, iPads and computers. iPhones don't allow full screen for web pages: there, use **Share → Add to Home Screen**; opened from the home screen icon, the tablet view has no browser bars (this works on every device and is the best option for a pulpit tablet).

## Which event the tablets show

The tablets show the location's name and the event they are on (e.g. *Vitrine · Zurich hall* / *Youth night · Wed 25 Nov*); the pulpit shows it under the guest's name. The vMix PC receives the location's upcoming events, and by default follows **today's or the next event** (*Automatic*). On the back tablet, the event menu under the title switches to another one, e.g. to prepare Sunday on Wednesday; choose *Automatic* again afterwards. All tablets follow the choice.

## Organizations, locations, events and people

- An **organization** has its staff, people, locations and library. With more than one, pick the one you work in at the top left of `/manage/`.
- A **location** is a venue or hall with one vMix PC (its agent): **Locations & tablets**.
- An **event** happens at a location over a period: **From** and **Until**. One day (both the same, the default), several days (a 3-day conference is *one* event), only a start (runs until you stop it), or neither (an ongoing event, e.g. "Sunday services"). It used to be called "service" and had one date.
- A **person** has **one permanent link** for every event. Their uploads, markers and library follow them to any location you add them to. Their link opens their next event; if they are in several, they switch at the top of the page.

The agent of a location receives its active events that are on, ended yesterday, or start within the next two weeks (`VITRINE_PRESYNC_DAYS`), and downloads their files **before the day**. Events that are on today come first, so the tablets open on them. The "ready for tomorrow?" alert runs the day before an event starts. Add someone to next month's event in Geneva and their files are on the Geneva vMix PC as soon as that event is within the window.

## Links are protected by a PIN

Every person has a 6-digit **PIN** next to their link (event page and person page). The first time a phone or computer opens the link, it asks for the PIN; that device is then **remembered until it is signed out**, so speakers type it only once. Their set and uploads stay available to them between events.

- **Send the PIN separately** from the link: say it, text it, or put it in a different message. **Copy link + PIN** on the person page copies both if you prefer one message.
- After **5 wrong PINs** the link locks for 15 minutes; the person page shows it and has **Unlock**.
- **Signed-in devices** on the person page lists every device (browser, system, when, IP). **Sign out** one, or **Sign out all**. A person can also sign out a phone that isn't theirs from their own page.
- **Change my PIN** on their page lets a person replace your PIN with their own (they type the current one first). Their own PIN is stored like a password: you can't see it any more, the person page shows "chosen by …" with when and from which device.
- **New PIN** gives them a new PIN you can see and send; devices already signed in stay signed in unless you sign them out.
- **Reset: let them choose** clears the PIN and signs out every device; the next phone that opens the link chooses a new PIN. Use it when someone forgot the PIN they chose.
- **New link** changes the link *and* signs out every device: use it when a link has leaked.
- Staff signed in to `/manage/` open people's pages without the PIN. The pulpit-tablet tips page needs no PIN.

## Before an event (backend, `/manage/`)

1. **Locations & tablets → Add a location**, then **Create agent token** for its vMix PC (once per venue).
2. **Events → New event** (name, From / Until, location). Clear *Until* for an event without an end, both for an ongoing one. Keep it **active** so the location's agent receives it. Tick **auto-approve** only for events where you trust every person.
3. **Add people** on the event page: pick someone you already know or create a new person. **Copy link**, or print/show the QR. **Open their set here** lets you fill a set for someone. **People** lists everyone with their upcoming events; a person's page has **New link** (the old one stops working), **Add to** another event, and their uploads.
4. People open their link on their phone:
   - **Use it**: when their set for this event is empty, they can start from their set of an earlier event.
   - **Upload**: photos, videos and audio from the gallery. Uploads resume after Wi-Fi dropouts while the page stays open.
   - **Library**: pick files others uploaded ("This event", "Earlier events", "My uploads", and "Shared" from other organizations) and **Add to my set**.
   - **My set**: order with the arrows; **Options** to remove a file, mark own uploads private, and add markers to videos (name + time, e.g. "Baptism · 1:12"). For a video, **Options** also shows a player: pause where you want a start point and press **Add marker here**. Markers show up as jump buttons on the tablets and in the cue panel.
   - **Tips: learn how to use the tablet at the pulpit** (link under the greeting, `/g/<token>/tablet/`): a short illustrated guide to the pulpit tablet: the name at the top, changing pages, how to tell what is live, the vMix indicator, tapping a photo, video and song controls, Resume / From start, and markers. Worth sending to speakers who haven't used it before.

Tip: videos that are already H.264 1920×1080 at your project frame rate (`VITRINE_VIDEO_FPS`) are ready in seconds, because they are copied instead of converted.
5. **Waiting for approval** on the event page: approve or reject. Rejected files never reach the vMix PC. Converted files appear on the tablets as soon as the agent has downloaded them.

## Settings (superusers)

**Settings** in `/manage/` holds what the whole server shares: how alerts are sent (Telegram bot, email server), when the "ready for tomorrow?" check runs, and where and when backups go (folder/NAS, S3, Azure, Google Drive, OpenDrive, FTP, SFTP, SMB or another rclone remote), with a connection test, **Back up now** and the backup history. Who receives alerts is chosen per organization on **Organization**.

On a phone, `/manage/` shows the sections as a row you swipe sideways, forms one field per line, and wide tables scroll sideways.

## Sharing between organizations

On **Organization**, tick the organizations that may use your shared files, and either share every approved, non-private file or only those you mark **Shared** (library: select files → **Share with other organizations**, or on a file's page). Their staff see them under **Library → Shared with us**, their people under **Shared** in the guest library. Their agents download a shared file only when one of their events uses it. Superusers create organizations and choose each organization's staff accounts there.

## Library housekeeping (`/manage/library/`)

Filter by name, guest, type, approval, or **not used for 30/90/365 days** ("used" = shown live from a tablet). Select files and **Move to trash**: if a file is still in a set of an active service you'll be warned first. Trash keeps files for 30 days (**Restore** or **Delete for good**). **Make private** hides a file from other guests' libraries; **Process again** re-runs conversion.

Each file's page has a preview, the original download, the sets it is in, and markers.

## Pairing tablets

Either in the backend (**Locations & tablets → Show pairing QR** on the location, choose Pulpit or Back) or in the tray companion (**Pair a tablet…**). Scan the QR with the tablet's camera while it is on the venue network. A code works once, for 15 minutes. Add the page to the home screen; on iPad use **Guided Access**, on Android **screen pinning**, so the pulpit can't leave the page.

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
