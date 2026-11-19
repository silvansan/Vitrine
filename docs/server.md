# Server setup (Portainer + Nginx Proxy Manager + Tailscale)

The server runs on your Linux box at home as one Portainer stack: Django (web), a worker for conversions, Postgres, Redis and nginx.

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

Only `/g/…` (guest links) needs to be public. If you prefer, add NPM access rules so `/manage/` and `/admin/` are only reachable from your own addresses; the agent uses the Tailscale port, not this address.

## 3. Agent access over Tailscale

The agent connects to `http://<server-tailscale-ip>:8080` (`VITRINE_TAILSCALE_PORT`). Check from the vMix PC:

```
curl http://100.101.102.103:8080/api/agent/manifest
```

`{"error": "unauthorized"}` means the path works; the agent sends its token.

## 4. First login

Open `https://vitrine.example.org/manage/` (or `http://<tailscale-ip>:8080/manage/`) and log in with the superuser. Then:

1. **Agents & tablets → Create agent token** for the vMix PC. Copy the token (shown once) into the agent's config.
2. **Services → New service**, then add guests and send them their links or QR codes.

## Storage and backups

Everything lives in two Docker volumes:

- `vitrine-data`: originals, converted files, thumbnails, the pairing key (`/data/keys`). Back this up.
- `vitrine-db`: Postgres.

Deleted files stay in the trash for `VITRINE_TRASH_DAYS` (default 30), then the worker removes them every night at 03:17.

## Settings reference

| Variable | Default | Meaning |
|---|---|---|
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
