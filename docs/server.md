# Server setup (Portainer + Nginx Proxy Manager + Tailscale)

The server runs on your Linux box at home as one Portainer stack: Django (web), a worker for conversions, Postgres, Redis and nginx.

## 1. Create the stack

1. Portainer → **Stacks → Add stack → Repository**.
2. Repository URL `https://github.com/silvansan/Vitrine`, reference `refs/heads/main`, compose path `deploy/docker-compose.yml`.
3. Under **Environment variables**, paste the contents of `deploy/.env.example` and change at least:
   - `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD`: long random strings.
   - `VITRINE_PUBLIC_URL`: the public address guests use, e.g. `https://vitrine.example.org`.
   - `DJANGO_CSRF_TRUSTED_ORIGINS`: the same address.
   - `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD`: your first login.
   - `VITRINE_VIDEO_FPS`: your vMix project frame rate (25 or 50 in Europe).
   - `VITRINE_BIND`: the server's Tailscale IP (e.g. `100.101.102.103`), so port 8080 is only reachable over Tailscale.
4. Deploy. Portainer builds the images from the repository (`server/` and `deploy/nginx/`); the first build takes a few minutes.

The stack joins the existing external network `proxy` (your Nginx Proxy Manager network). If yours has another name, change it at the bottom of the compose file.

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
| `VITRINE_PUBLIC_URL` | `http://localhost:8000` | Base of guest links |
| `VITRINE_VIDEO_FPS` | `25` | Frame rate videos are converted to |
| `VITRINE_FILMSTRIP_INTERVAL` | `5` | Seconds between cue-panel frames |
| `VITRINE_MAX_UPLOAD_MB` | `4096` | Largest upload |
| `VITRINE_TRASH_DAYS` | `30` | Days before trash is emptied |
| `VITRINE_TAILSCALE_PORT` / `VITRINE_BIND` | `8080` / `0.0.0.0` | Where the agent reaches the server |
| `TZ` | `Europe/Zurich` | Time zone for dates |

## Updating

Portainer → the stack → **Pull and redeploy** (tick *Re-pull image and redeploy* so it rebuilds from the latest commit). Migrations run on start.

GitHub Actions also publishes `ghcr.io/silvansan/vitrine-server`; to use it instead of building, replace `image: vitrine-server:local` with that name (log Portainer in to ghcr.io if the package is private).
