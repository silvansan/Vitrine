# Agent on the vMix PC

The agent is one Windows service (`vitrine-agent.exe`) plus a tray icon (`vitrine-tray.exe`). It downloads the files, controls vMix, and serves the tablet pages on the venue network. It uses almost no CPU: conversions happen on the server.

## 1. Prepare vMix

- **Settings → Web Controller**: enable it (port 8088). The agent only talks to `127.0.0.1:8088`, so no password is needed.
- Decide which **mix** feeds the projector (e.g. Mix 2) and which input is the **default** (e.g. your banner).
- Optional: add two **List** inputs named `GM_A` and `GM_B` and save them in your preset. If they're missing, the agent tries to create them on the first take.

## 2. Install

1. Download `vitrine-agent.exe` and `vitrine-tray.exe` from the latest GitHub release (or the build artifacts) into e.g. `C:\Program Files\Vitrine\`.
2. Open an **administrator** command prompt in that folder and run:
   ```
   vitrine-agent install
   ```
   The first time, this creates `C:\ProgramData\Vitrine\config.json` and stops. Edit it (see below), then run `vitrine-agent install` again.
3. Install registers the `VitrineAgent` service (starts at boot, restarts itself after a crash), opens TCP port 8090 in the Windows firewall for private networks, and starts the tray icon at every logon.

### config.json

```json
{
  "server_url": "http://100.101.102.103:8080",
  "agent_token": "vta_…",
  "media_dir": "D:\\Vitrine\\media",
  "vmix_url": "http://127.0.0.1:8088",
  "listen": ":8090",
  "advertise_ip": "",
  "mix_param_offset": -1,
  "live_throttle_kbps": 4096
}
```

| Key | Meaning |
|---|---|
| `server_url` | The server over Tailscale |
| `agent_token` | From the backend (Agents & tablets) |
| `media_dir` | Where files are kept; vMix plays them from here. Use a local disk |
| `listen` | Port for the tablets |
| `advertise_ip` | LAN address put in pairing QR codes; empty = detect (Tailscale addresses are skipped) |
| `mix_param_offset` | vMix's API `Mix` parameter = mix number + this. With `-1`, Mix 2 is sent as `Mix=1` (vMix numbers mixes from 0 = main). Check with the companion's **Show default on the projector** button; if the wrong mix switches, change this to `0` |
| `live_throttle_kbps` | Download limit while something is live; `0` = no limit |

After changing the config: `vitrine-agent stop` and `vitrine-agent start` (or restart the service).

Commands: `install`, `uninstall`, `start`, `stop`, `run` (in the console, for testing), `version`. Log file: `C:\ProgramData\Vitrine\agent.log`.

## 3. Companion (tray icon → Open settings)

The companion is a page at `http://127.0.0.1:8090/companion` (also reachable from the back tablet). It sets, for this PC:

- **Session**: which active service the tablets show.
- **Projector output**: the mix the tablets switch.
- **Default input**: chosen by number and name from vMix's live input list. The agent stores vMix's input key, so the right input is found even if numbers shift; if the banner changes, pick the new one here.
- **Transition** and duration, **go back to default when a video ends**, **jump mode** (crossfade between GM_A/GM_B, or jump in place), input names.
- **Tablets**: the paired list, unpair, and **Show pairing QR** (signed by the server when online, by the agent itself when the server is unreachable).

The tray icon turns red while guest media is live and grey when vMix or the agent isn't reachable.

## Files on disk

```
D:\Vitrine\media\<guest>\<id>-<name>.jpg|.mp4    played by vMix
C:\ProgramData\Vitrine\thumbs\                     tablet thumbnails and filmstrips
C:\ProgramData\Vitrine\state.json                  settings, tablets, last manifest, positions
```

Files that leave all active services are removed at the next sync (unless they're on air).

## Testing without vMix

`vitrine-fakevmix.exe` pretends to be vMix on port 8088 (inputs "Banner Sunday" and "Camera 1", Mix 2–4) and logs every function call. Stop real vMix first, run the fake, then `vitrine-agent run`.

## Running the agent on another PC

Not the recommended setup, but possible: set `vmix_url` to `http://<vmix-pc>:8088`, enable the Web Controller on the network, and make sure the vMix PC can read `media_dir` at the same path (for example a share mapped to the same drive letter). See the discussion in docs/architecture.md.
