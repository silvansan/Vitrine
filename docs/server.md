# Server setup (Portainer + Nginx Proxy Manager + Tailscale)

The server runs on your Linux box at home as one Portainer stack: Django (web), a worker for conversions, a scheduler (alerts, backups, trash), Postgres, Redis and nginx.

## 1. Create the stack

1. Portainer → **Stacks → Add stack → Repository**.
2. Repository URL `https://github.com/silvansan/Vitrine`, reference `refs/heads/main`, compose path `deploy/docker-compose.yml`. (Pasting `deploy/docker-compose.yml` into the **Web editor** works too.)
3. Under **Environment variables**, use **Load variables from .env file** with `deploy/.env.example` (or paste its uncommented lines) and change:
   - `POSTGRES_PASSWORD`: a long random string. **Keep it**: the database is created with it on the first start, and a different value later locks Vitrine out of its data.
   - `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD`: your first login (only used on the first start).
   - `VITRINE_PUBLIC_URL`: optional. Guest links are built from the address you open `/manage/` with; set this (e.g. `https://vitrine.yourdomain.org`) only if you manage Vitrine through another address, such as the Tailscale IP, than the one guests use.
   - `VITRINE_PROXY_NETWORK`: your Nginx Proxy Manager network.
   - `VITRINE_BIND`: the server's Tailscale IP (e.g. `100.101.102.103`), so port 8080 is only reachable over Tailscale.

   Telegram, email and backups don't go here: set them in `/manage/` → **Settings** after the first sign-in. The optional variables (frame rate, upload size…) are commented out in the file with their defaults. Empty variables are fine: they mean "use the default". `DJANGO_SECRET_KEY` is optional too; without it Vitrine makes one on the first start and keeps it in the data volume.
4. Deploy. Portainer pulls `ghcr.io/silvansan/vitrine-server` and `ghcr.io/silvansan/vitrine-nginx`, which GitHub Actions builds on every push to `main`. Nothing is built on the server.

If the pull fails with "denied" or "unauthorized", the packages are private: either make them public on GitHub (your profile → **Packages** → the package → **Package settings** → **Change visibility**), or add `ghcr.io` under Portainer → **Registries** with your GitHub username and a token with `read:packages`.

The stack joins the existing external network `proxy` (your Nginx Proxy Manager network). If yours has another name (e.g. `nginx-proxy_default`), set `VITRINE_PROXY_NETWORK` to it in the environment variables. NPM must be attached to that network so it can reach `vitrine-nginx` by name.

## 2. Public guest address (Nginx Proxy Manager)

Add a proxy host:

- Domain: your guest subdomain, e.g. `vitrine.example.org`
- Forward to `vitrine-nginx`, port `80`, scheme `http`
- Enable **Websockets support**
- SSL: request a Let's Encrypt certificate, force SSL
- Advanced → custom config: `client_max_body_size 64m;` (uploads arrive in 8 MB pieces)

Only `/g/…` (guest links) needs to be public. If you prefer, add NPM access rules so `/login/`, `/manage/` and `/admin/` are only reachable from your own addresses; the agent uses the Tailscale port, not this address.

## 3. Agent access over Tailscale

The agent connects to `http://<server-tailscale-ip>:8080` (`VITRINE_TAILSCALE_PORT`). Check from the vMix PC:

```
curl http://100.101.102.103:8080/api/agent/manifest
```

`{"error": "unauthorized"}` means the path works; the agent sends its token.

## 4. First login

Open `https://vitrine.example.org/manage/` (or `http://<tailscale-ip>:8080/manage/`) and sign in with the superuser (the Vitrine sign-in page at `/login/` is also used by `/admin/`; **Sign out** is at the top right of `/manage/` and of `/admin/`). `/admin/` is styled like the rest of Vitrine (light and dark) and has **← Back to Vitrine**; it is only for raw data, day-to-day work happens in `/manage/`. Add your team's accounts under **Team** in `/manage/`, and change your own password under **My account**. Then:

1. **Locations & tablets → Add a location** (e.g. "Main hall"), then **Create agent token** for its vMix PC. Copy the token (shown once) into the agent's settings page. An organization "Main" is created for you on first login.
2. **Events → New event** at that location, then add people and send them their links or QR codes.

Upgrading to a version with the **scheduler** service (alerts and backups): if the stack uses the repository's compose file, Portainer picks it up when you redeploy; if you pasted the compose file into the web editor, paste the new one. Without the scheduler, conversions still work but there are no alerts, no backups and no trash cleanup.

Upgrading from a version without organizations: the database migration puts everything into one organization "Main", gives each existing agent a location of the same name, moves every service (now "event") to the first location, and turns guests into people. Guests with the same name become one person; their other links keep working.

## Storage and backups

Everything lives in two Docker volumes:

- `vitrine-data`: originals, converted files, thumbnails, the pairing key (`/data/keys`). Back this up.
- `vitrine-db`: Postgres.

Deleted files stay in the trash for `VITRINE_TRASH_DAYS` (default 30), then the scheduler removes them every night at 03:17.

## Settings reference

Stack environment variables. Empty or missing means the default. The alert and backup ones are only used when the same setting is left empty in `/manage/` → Settings, which is the easier place for them.

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | required | Database password, fixed after the first start |
| `DJANGO_SECRET_KEY` | made on first start | Signs staff sessions; changing it signs staff out |
| `VITRINE_BACKUP_TARGET` | empty | Where nightly backups go (see [Backups](#backups)); empty = off |
| `VITRINE_BACKUP_TIME` / `VITRINE_BACKUP_KEEP_DAYS` | `03:30` / `14` | When to back up; how many days of database dumps to keep |
| `VITRINE_TELEGRAM_BOT_TOKEN`, `EMAIL_HOST`… | empty | Alerts to the team (see [Alerts](#alerts-to-the-team)) |
| `VITRINE_OFFLINE_ALERT_MINUTES` / `VITRINE_SYNC_CHECK_TIME` | `10` / `18:00` | When a vMix PC counts as offline; when to check tomorrow's files |
| `VITRINE_PRESYNC_DAYS` | `14` | How many days ahead an agent receives its location's events (and downloads their files) |
| `VITRINE_PUBLIC_URL` | empty | Base of guest links; empty = the address /manage/ was opened with |
| `VITRINE_VIDEO_FPS` | `25` | Frame rate videos are converted to |
| `VITRINE_VIDEO_PRESET` | `ultrafast` | x264 speed for converting videos: `ultrafast` (ready soonest, files about 2–3× bigger), `veryfast`, `medium` (smallest files, slowest). **Settings → Storage** overrides it |
| `VITRINE_FILMSTRIP_INTERVAL` | `5` | Seconds between cue-panel frames |
| `VITRINE_MAX_UPLOAD_MB` | `4096` | Largest upload per file, unless set in **Settings → Storage** (in GB there). A file over the limit, or an empty one (a cloud file not downloaded yet), is refused with a message saying which. |
| `VITRINE_TRASH_DAYS` | `30` | Days before trash is emptied |
| `VITRINE_TAILSCALE_PORT` / `VITRINE_BIND` | `8080` / `0.0.0.0` | Where the agent reaches the server |
| `TZ` | `Europe/Zurich` | Time zone for dates |

## Troubleshooting

- **Files stay on "Processing"**: conversions run in the `worker` container. Its log should show `Task guestmedia.tasks.process_asset[…] received` and `succeeded` for each upload. Fix whatever the log shows, then select the files in `/manage/library/` and press **Process again**.
- **Photos take a second or two; videos take about as long as the video** (one at a time).

## Updating

1. **GitHub → Actions → release → Run workflow** (patch by default). It tags the next version, creates the release with the agent `.exe` files, and publishes the server images as `:latest` and `:vX.Y.Z`. Wait until both the *release* and the *build* run inside it are green (about 5–10 minutes).
2. **Portainer → the stack → Pull and redeploy** (tick *Re-pull image and redeploy*). Migrations run on start. If you pasted the compose file into the web editor, compare it with `deploy/docker-compose.yml` first and paste the new one if it changed.
3. **Each vMix PC**: download `vitrine-agent.exe` (and `vitrine-tray.exe`) from the release and double-click it; it updates the service in place and keeps its settings, pairings and files. Server and agent should run the same version: a newer server with an older agent works, but new features that need the agent (for example guest control) stay off until it is updated.
4. Check: `/manage/` → Locations & tablets shows each vMix PC online with the new version.

Static files (CSS, JS) get a fingerprint in their name at every start (`manage.3f2a….css`), so phones and tablets pick up a new design right after a redeploy instead of showing a cached old one.

Between releases, every push to `main` also builds `:latest` (the **build** workflow), so *Pull and redeploy* alone picks up server-only fixes.

### Notes for v0.1.9 (guest control, permanent tablet link)

- Redis must stay in the stack (it is in the compose file): guests' commands and the live state of each vMix PC go through its cache. Without `REDIS_URL` the server falls back to a per-process cache and guest control only works with a single web process.
- Nothing to change in Nginx Proxy Manager: the guest tablet view polls over normal HTTPS. The agent's WebSocket (`/ws/agent/`) is the same as before; if your agent reaches the server through NPM rather than Tailscale, *Websockets Support* must be ticked on that proxy host (it already had to be).
- The migration gives each location a random tablet link (`/t/<code>/`). The link redirects to `http://<vMix PC LAN IP>:8090/screen`, so it only opens on the venue network; that is expected.

To pin a release instead of following `main`, tag it (`v1.2.0`); the workflow publishes both images with that tag, and you change `:latest` to `:v1.2.0` in the stack.

For local development, build the images yourself: `docker build -t ghcr.io/silvansan/vitrine-server:latest server` and `docker build -t ghcr.io/silvansan/vitrine-nginx:latest deploy/nginx`.

## Alerts to the team

Vitrine can tell your team when something needs them, by **Telegram** and/or **email**:

| Alert | When |
|---|---|
| **Uploads waiting for approval** | About every 2 minutes. In Telegram, each upload comes as its own message (thumbnail, who, file name, length, event) with **✅ Approve**, **❌ Reject** and **Open** buttons; after 10 files the rest is one link. By email: one summary with a link. |
| **Someone asks to control the screen** | A guest presses *Ask for access* on their own tablet view (at most once per person and event every 10 minutes). Accept or refuse it on the back tablet. |
| **A file couldn't be converted** | Right after the conversion fails: file, who sent it, the error, and a link. Try again with Library → select it → *Process again*. |
| **First upload from someone** | The first file a person ever uploads (handy to know a new link works). |
| **Morning summary** | On days with an event, at the *morning summary* time (08:00): per event the vMix PC status, who has files in their set and how many, who has none yet, what waits for approval and what failed. |
| **vMix PC offline** | Offline for `VITRINE_OFFLINE_ALERT_MINUTES` (10) while its location has an event today or tomorrow, and when it is back. |
| **Ready for tomorrow?** | At `VITRINE_SYNC_CHECK_TIME` (18:00) the day before an event: are all its files on that location's vMix PC, are some still waiting for approval? |
| **Backup failed / disk nearly full** | When it happens (disk: at most every 12 hours). |

Each organization chooses which of these it wants on **Organization → Alerts to the team**.

**Approving from Telegram**: anyone in the linked chat can press Approve or Reject; the message then shows *✅ Approved by Ana* (or *❌ Rejected by …*) and its buttons go away, so the others see it was handled. Presses from any other chat are refused. The scheduler reads the presses every few seconds (with `getUpdates`, so no webhook and nothing reachable from outside is needed); without the scheduler running, the buttons don't react.

Everything is set up in the web interface; nothing has to go into the stack.

**1. How messages are sent** (superusers, **Settings → Alerts: how they are sent**):

- **Telegram**: in Telegram open **@BotFather**, send `/newbot`, pick a name (e.g. "Vitrine alerts") and a username ending in `bot`, and paste the token. On saving, Vitrine checks it and shows *Bot @… is ready*.
- **Email**: pick your **Provider** to fill in server, port and security, then enter user, password and sender:
  - **Brevo**: server `smtp-relay.brevo.com`, port 587, STARTTLS. *User* is the SMTP login from Brevo → Settings → SMTP & API (an address like `123abc@smtp-brevo.com`, or your account email); *Password* is an **SMTP key** from the same page (an API key doesn't work); *From* must be a sender verified in Brevo.
  - Infomaniak, Gmail / Google Workspace (needs an app password) and Microsoft 365 have presets too; anything else: choose *Other* and enter your provider's SMTP details.
  - **Save and send a test email** sends to the address you type (your account's email by default).
- **When**: minutes before a vMix PC counts as offline, the time of the "ready for tomorrow?" check, and the time of the morning summary on event days.

The stack variables `VITRINE_TELEGRAM_BOT_TOKEN`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` still work and are only used when the Settings fields are empty; leaving them empty in the stack is fine.

**2. Who gets them** (each organization, **Organization → Alerts to the team**):

- **Connect Telegram**: shows *Add the bot to a group* (Telegram asks which group) and *Open the bot* (your own chat; also as a QR code for your phone). Once the bot is added or you press *Start*, the page notices within a few seconds, the bot posts "✅ Vitrine alerts for … will arrive in this chat" and the page shows *Alerts go to <group name>*. The link works for 15 minutes. **Disconnect** or **Connect another chat** change it later; a chat id can still be entered by hand under *Enter a chat id by hand*.
- **Email addresses**, one per line or separated by commas.
- Which alerts to send, then **Save and send a test**.

How the linking works: the link carries a one-time code (`/start <code>`); the server reads the bot's messages with Telegram's `getUpdates` (no webhook, so nothing has to reach your server from outside) and saves the chat that sent the code. When a group is upgraded to a supergroup its id changes; Vitrine follows the move by itself.

## Backups

The **scheduler** container backs up every night (03:30 by default) with [rclone](https://rclone.org), so the target can be almost anything. Set it up in **Settings → Backups** (superusers): choose where to, fill in the connection details, **Save and test the connection**, and **Back up now** for a first run. Secrets are stored in the database and never shown again; leave a password field as it is to keep it.

| In Settings | What you fill in |
|---|---|
| Folder on the server | A path such as `/backup`; mount your USB disk or NAS folder there in the compose file (`scheduler` → `volumes`) |
| S3-compatible | Endpoint (e.g. `https://fsn1.your-objectstorage.com` for Hetzner), access key, secret key; path `bucket/folder` |
| Azure Blob Storage | Storage account name and key; path `container/folder` |
| Google Drive | A token: install rclone on a computer with a browser, run `rclone authorize "drive"`, sign in with the Google account, paste the `{"access_token": …}` it prints. Or a service account key for a Workspace shared drive. Optional folder id |
| OpenDrive | Username (email) and password |
| FTP / FTPS, SFTP | Host, port, user, password (FTPS: tick *Use TLS*) |
| SMB / Windows share / NAS | Host or IP, user, password, optional domain; path `share/folder` |
| Other rclone remote | Any of rclone's storage systems (Dropbox, OneDrive, pCloud, Mega, WebDAV, Box…): paste its section from `rclone.conf` and enter `name:folder` as the path |

The backup time, how many days of database dumps to keep, and the "ready for tomorrow?" check time are in Settings too.

Instead of Settings you can configure backups in the stack environment, which is used while Settings says *As set in the stack environment*:

| Target | `VITRINE_BACKUP_TARGET` | Remote settings |
|---|---|---|
| Folder on the server (USB disk, NAS mounted on the host) | `/backup` | Uncomment the `/backup` volume of `scheduler` in the compose file |
| S3-compatible (Hetzner Object Storage, AWS, MinIO, Wasabi, Backblaze B2…) | `s3:my-bucket/vitrine` | `RCLONE_CONFIG_S3_TYPE=s3`, `…_PROVIDER=Other`, `…_ENDPOINT`, `…_ACCESS_KEY_ID`, `…_SECRET_ACCESS_KEY` |
| Azure Blob Storage | `azure:container/vitrine` | `RCLONE_CONFIG_AZURE_TYPE=azureblob`, `…_ACCOUNT`, `…_KEY` |
| FTP / FTPS | `ftp:backups/vitrine` | `RCLONE_CONFIG_FTP_TYPE=ftp`, `…_HOST`, `…_USER`, `…_PASS`, `…_EXPLICIT_TLS=true` |
| SFTP | `sftp:backups/vitrine` | `RCLONE_CONFIG_SFTP_TYPE=sftp`, `…_HOST`, `…_USER`, `…_PASS` |
| SMB / Windows share / NAS (no host mount) | `nas:share/vitrine` | `RCLONE_CONFIG_NAS_TYPE=smb`, `…_HOST`, `…_USER`, `…_PASS` |

The part before `:` is the remote's name; `RCLONE_CONFIG_<NAME>_<OPTION>` variables define it (examples in `deploy/.env.example`). FTP, SFTP and SMB passwords must be *obscured* first: `docker run --rm rclone/rclone obscure 'your-password'`. Instead of variables you can also put an `rclone.conf` at `/data/rclone.conf` in the data volume.

What is copied:

- `db/vitrine-YYYYMMDD-HHMM.dump`: the database (PostgreSQL custom format), one per night, kept `VITRINE_BACKUP_KEEP_DAYS` (14),
- `media/`: all uploads, converted files and thumbnails, mirrored; files that leave it go to `media-removed/<date>/` and are kept 30 days,
- `keys/`: the pairing key that signs the tablets' QR codes.

**Settings → Backup history** shows the last runs with their logs. The Events page warns superusers when backups are off or the last success is older than 36 hours, and the organizations that want it get an alert when one fails.

## Storage and more space

**Settings → Storage** (superusers) shows how full the disk is, what takes the space (uploads as received, converted files and thumbnails, uploads in progress, a server-move archive, the database, the trash) and the uploads per organization. When a disk is over 90 % full or has less than 5 GB free, the Events page shows a warning and the organizations that get backup alerts get a Telegram/email message (at most every 12 hours). A full disk stops uploads and then the database, so act on it.

### More space: a Hetzner volume

Uploads and converted videos are the big part. They can live on a separate Hetzner volume, which you can make bigger at any time without touching the server:

1. **Hetzner Cloud Console → Volumes → Create volume**: the size you need, the **same location** as the server, attach it to the server, mount **Automatic**, file system **ext4**. Over SSH, `df -h` then shows it at `/mnt/HC_Volume_<number>`.
2. Find Vitrine's current volumes: `docker volume ls | grep vitrine` (they are called `<stack name>_vitrine-data` and `<stack name>_vitrine-db`).
3. **Stop the stack** in Portainer.
4. Copy the files (and, if you like, the database) to the new disk, keeping owners and permissions:
   ```
   rsync -aHAX --info=progress2 /var/lib/docker/volumes/<stack>_vitrine-data/_data/ /mnt/HC_Volume_<number>/vitrine-data/
   rsync -aHAX --info=progress2 /var/lib/docker/volumes/<stack>_vitrine-db/_data/   /mnt/HC_Volume_<number>/vitrine-db/
   ```
5. Add to the stack variables (the second only if you copied the database) and **Update the stack**:
   ```
   VITRINE_DATA_PATH=/mnt/HC_Volume_<number>/vitrine-data
   VITRINE_DB_PATH=/mnt/HC_Volume_<number>/vitrine-db
   ```
6. Check **Settings → Storage**: it now says *Files disk* (and shows the system disk separately), with the same number of files as before.
7. After a few days of normal use, delete the old Docker volumes to free the server's disk: `docker volume rm <stack>_vitrine-data` (and `…_vitrine-db` if you moved it). Not before: they are your way back.

To grow the volume later: Hetzner Console → the volume → **Resize**, then on the server `resize2fs /dev/disk/by-id/scsi-0HC_Volume_<number>`; no restart needed.

If the volume ever isn't mounted when the server starts (detached in the console, for example), Docker would create an empty folder in its place and Vitrine would look empty: Settings → Storage would say *Server disk* again. Attach it, check `df -h`, and restart the stack; nothing is lost.

Signing in over plain http (for example `http://100.x.x.x:8080` over Tailscale, before DNS and the certificate are ready) works. If an older version answers *CSRF verification failed* there, add that exact address to `DJANGO_CSRF_TRUSTED_ORIGINS` (e.g. `http://100.101.102.103:8080`) and update the stack.

### Moving to another server

**Settings → Move to another server** (superusers) packs everything into one archive and brings it in on the new server, without any commands:

1. **Old server**: update it to the latest version (Pull and redeploy), then **Prepare full backup**. A progress bar shows the packing; it needs as much free disk space as all files together, and uploads keep working meanwhile. The archive (`vitrine-full-YYYYMMDD-HHMM.tar`) holds the database (people, events, sets, settings, staff accounts), every file and the tablets' pairing key.
2. **New server**: deploy the stack (same compose file, its own `POSTGRES_PASSWORD`), sign in with the first superuser from its stack variables, open **Settings → Move to another server**.
3. Bring the archive in, either way:
   - **From the link** (best for big libraries: server to server, nothing passes through your computer): on the old server press **Copy link for the new server**, paste it on the new one, type **REPLACE**, **Import from the link**. The link works for 48 hours without signing in, so only paste it there.
   - **Upload the file**: **Download** it on the old server, then on the new one type REPLACE and choose the `.tar`; the upload resumes if the connection drops.
4. Files arrive first, then the database is replaced in one go (if anything fails, the database stays as it was). You are signed out at that moment: sign in with an account **from the old server**.
5. **Restart the stack** (Portainer → Stop, Start) so every part uses the imported pairing key; tablets then stay paired.
6. If the address changed, update the vMix PCs (tray → Settings → server address) and the DNS / Nginx Proxy Manager host. Turn off backups on the old server (or switch it off), since the new one now backs up to the same target with the same settings. Back on the old server, **Delete archive** to free the space.

### Restore from a backup

1. Deploy the stack fresh (or stop `web`, `worker` and `scheduler`).
2. Copy the files back into the `vitrine-data` volume: `media/` to `/data/media/` and `keys/pairing_ed25519.pem` to `/data/keys/`. With rclone, from any container that has the same `RCLONE_CONFIG_…` variables and the volume mounted: `rclone copy s3:my-bucket/vitrine/media /data/media` and `rclone copy s3:my-bucket/vitrine/keys /data/keys`.
3. Restore the database into the `db` container: copy the dump in (`docker cp vitrine-….dump <db container>:/tmp/`) and run `pg_restore -U vitrine -d vitrine --clean --if-exists --no-owner /tmp/vitrine-….dump`.
4. Start the stack again. Agents and tablets reconnect by themselves.

## Documentation on the server (`/docs/`)

Every deployment serves this documentation at `https://<your-vitrine>/docs/`: the README and every page of the repo's `docs/` folder, with a search box (press `/`). It needs no login, so new team members can read the setup before they have an account. The sign-in page and the manage pages link to it.

The GitHub build copies `docs/` and `README.md` into the server image. To add a page, put a new `.md` file in `docs/`; it appears in the sidebar after the built-in pages. If you build the image yourself, copy them first: `cp -r docs server/docs && cp README.md server/docs/`.
