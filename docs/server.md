# Server setup (Portainer + Nginx Proxy Manager + Tailscale)

The server runs on your Linux box at home as one Portainer stack: Django (web), a worker for conversions, a scheduler (alerts, backups, trash), Postgres, Redis and nginx.

## 1. Create the stack

1. Portainer → **Stacks → Add stack → Repository**.
2. Repository URL `https://github.com/silvansan/Vitrine`, reference `refs/heads/main`, compose path `deploy/docker-compose.yml`. (Pasting `deploy/docker-compose.yml` into the **Web editor** works too.)
3. Under **Environment variables**, paste the contents of `deploy/.env.example` and change at least:
   - `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD`: long random strings.
   - `VITRINE_PUBLIC_URL`: optional. Guest links are built from the address you open `/manage/` with; set this (e.g. `https://vitrine.yourdomain.org`) only if you manage Vitrine through another address, such as the Tailscale IP, than the one guests use.
   - `DJANGO_CSRF_TRUSTED_ORIGINS`: the same address.
   - `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD`: your first login.
   - `VITRINE_VIDEO_FPS`: your vMix project frame rate (25 or 50 in Europe).
   - `VITRINE_BIND`: the server's Tailscale IP (e.g. `100.101.102.103`), so port 8080 is only reachable over Tailscale.
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

Open `https://vitrine.example.org/manage/` (or `http://<tailscale-ip>:8080/manage/`) and sign in with the superuser (the Vitrine sign-in page at `/login/` is also used by `/admin/`; **Sign out** is at the top right of `/manage/` and of `/admin/`). `/admin/` is styled like the rest of Vitrine (light and dark) and has **← Back to Vitrine**; it is only for raw data, day-to-day work happens in `/manage/`. Then:

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

| Variable | Default | Meaning |
|---|---|---|
| `VITRINE_BACKUP_TARGET` | empty | Where nightly backups go (see [Backups](#backups)); empty = off |
| `VITRINE_BACKUP_TIME` / `VITRINE_BACKUP_KEEP_DAYS` | `03:30` / `14` | When to back up; how many days of database dumps to keep |
| `VITRINE_TELEGRAM_BOT_TOKEN`, `EMAIL_HOST`… | empty | Alerts to the team (see [Alerts](#alerts-to-the-team)) |
| `VITRINE_OFFLINE_ALERT_MINUTES` / `VITRINE_SYNC_CHECK_TIME` | `10` / `18:00` | When a vMix PC counts as offline; when to check tomorrow's files |
| `VITRINE_PRESYNC_DAYS` | `14` | How many days ahead an agent receives its location's events (and downloads their files) |
| `VITRINE_PUBLIC_URL` | empty | Base of guest links; empty = the address /manage/ was opened with |
| `VITRINE_VIDEO_FPS` | `25` | Frame rate videos are converted to |
| `VITRINE_FILMSTRIP_INTERVAL` | `5` | Seconds between cue-panel frames |
| `VITRINE_MAX_UPLOAD_MB` | `4096` | Largest upload |
| `VITRINE_TRASH_DAYS` | `30` | Days before trash is emptied |
| `VITRINE_TAILSCALE_PORT` / `VITRINE_BIND` | `8080` / `0.0.0.0` | Where the agent reaches the server |
| `TZ` | `Europe/Zurich` | Time zone for dates |

## Troubleshooting

- **Files stay on "Processing"**: conversions run in the `worker` container. Its log should show `Task guestmedia.tasks.process_asset[…] received` and `succeeded` for each upload. Fix whatever the log shows, then select the files in `/manage/library/` and press **Process again**.
- **Photos take a second or two; videos take about as long as the video** (one at a time).

## Updating

Push to `main` and wait for the **build** workflow to finish, then Portainer → the stack → **Pull and redeploy** (tick *Re-pull image and redeploy*). Migrations run on start.

To pin a release instead of following `main`, tag it (`v1.2.0`); the workflow publishes both images with that tag, and you change `:latest` to `:v1.2.0` in the stack.

For local development, build the images yourself: `docker build -t ghcr.io/silvansan/vitrine-server:latest server` and `docker build -t ghcr.io/silvansan/vitrine-nginx:latest deploy/nginx`.

## Alerts to the team

Vitrine can tell your team when something needs them, by **Telegram** and/or **email**:

- uploads waiting for approval (one message for several files, about every 2 minutes),
- a vMix PC offline for `VITRINE_OFFLINE_ALERT_MINUTES` (10) while its location has an event today or tomorrow, and when it is back,
- at `VITRINE_SYNC_CHECK_TIME` (18:00) the day before an event: are all its files on that location's vMix PC, are some still waiting for approval?
- a failed backup.

Set up once in **Settings → Alerts: how they are sent** (superusers): a Telegram bot token (create a bot with @BotFather) and/or your SMTP server, with **Save and email me a test**. The stack variables `VITRINE_TELEGRAM_BOT_TOKEN`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` still work and are used when the Settings fields are empty. Then each organization chooses its Telegram chat (group) id, email addresses and which alerts it wants on **Organization → Alerts to the team**; **Save and send a test** checks it works. To find a group's chat id: add the bot to the group, write something, open `https://api.telegram.org/bot<token>/getUpdates` and copy `chat.id` (it starts with `-100`).

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

### Restore from a backup

1. Deploy the stack fresh (or stop `web`, `worker` and `scheduler`).
2. Copy the files back into the `vitrine-data` volume: `media/` to `/data/media/` and `keys/pairing_ed25519.pem` to `/data/keys/`. With rclone, from any container that has the same `RCLONE_CONFIG_…` variables and the volume mounted: `rclone copy s3:my-bucket/vitrine/media /data/media` and `rclone copy s3:my-bucket/vitrine/keys /data/keys`.
3. Restore the database into the `db` container: copy the dump in (`docker cp vitrine-….dump <db container>:/tmp/`) and run `pg_restore -U vitrine -d vitrine --clean --if-exists --no-owner /tmp/vitrine-….dump`.
4. Start the stack again. Agents and tablets reconnect by themselves.

## Documentation on the server (`/docs/`)

Every deployment serves this documentation at `https://<your-vitrine>/docs/`: the README and every page of the repo's `docs/` folder, with a search box (press `/`). It needs no login, so new team members can read the setup before they have an account. The sign-in page and the manage pages link to it.

The GitHub build copies `docs/` and `README.md` into the server image. To add a page, put a new `.md` file in `docs/`; it appears in the sidebar after the built-in pages. If you build the image yourself, copy them first: `cp -r docs server/docs && cp README.md server/docs/`.
