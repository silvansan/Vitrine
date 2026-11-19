# Agent on the vMix PC

The agent is one Windows service (`vitrine-agent.exe`) plus a tray icon (`vitrine-tray.exe`). It downloads the files, controls vMix, and serves the tablet pages on the venue network. It uses almost no CPU: conversions happen on the server.

## 1. Prepare vMix

- **Settings → Web Controller**: enable it (port 8088). The agent only talks to `127.0.0.1:8088`, so no password is needed.
- Decide which **mix** feeds the projector (e.g. Mix 2) and which input is the **default** (e.g. your banner).
- Optional: add two **List** inputs named `GM_A` and `GM_B` and save them in your preset. If they're missing, the agent tries to create them on the first take.

## 2. Install or update

1. Download the agent zip (from the latest GitHub release or the build artifacts) and unzip it anywhere, e.g. Downloads.
2. **Double-click `vitrine-agent.exe`** and allow the Windows permission prompt. It:
   - stops the agent and tray if an older version is running,
   - copies itself and `vitrine-tray.exe` to `C:\Program Files\Vitrine\`,
   - installs (or updates) the `VitrineAgent` service, which starts at boot and restarts itself after a crash,
   - opens TCP port 8090 in the Windows firewall for all network types (venue networks are often marked Public),
   - starts the tray icon (and sets it to start at every logon),
   - opens the settings page in your browser.
3. In the settings page, under **Connection**, paste the **server address** (e.g. `https://vitrine.yourdomain.ch`) and the **agent token** from the backend (Agents & tablets), then **Save and restart agent**. The agent checks the connection before saving.

**Updating** is the same: unzip the new version and double-click its `vitrine-agent.exe`. Your settings, tablets and downloaded files are kept (they live in `C:\ProgramData\Vitrine\`).

### Tray menu

| Item | Does |
|---|---|
| Open settings… | Connection, projector mix, default input, tablets, status, log |
| Pair a tablet… | Shows a pairing QR code |
| Open media folder / Open log | Opens them in Explorer / Notepad |
| Restart agent | Restarts the agent (no admin rights needed); tablets reconnect by themselves |
| Start agent / Stop agent | Starts or stops the Windows service; Windows asks for permission |
| Close tray icon | Closes only the icon; the agent keeps running |

### Connection settings

All of these are in **Open settings… → Connection** (the first two) and **More connection settings**. They are saved in `C:\ProgramData\Vitrine\config.json`, which you can still edit by hand if you prefer (then use **Restart agent**). They can only be changed on the vMix PC itself, not from the back tablet.

| Setting | config.json key | Meaning |
|---|---|---|
| Server address | `server_url` | The Vitrine server: your public domain, or `http://<tailscale-ip>:8080` |
| Agent token | `agent_token` | From the backend (Agents & tablets); shown only as its first characters |
| Media folder | `media_dir` | Where files are kept; vMix plays them from here. Use a local disk |
| vMix address | `vmix_url` | Keep `http://127.0.0.1:8088` when the agent runs on the vMix PC |
| Tablet port | `listen` | Port for the tablets (default 8090) |
| Address in pairing QR codes | `advertise_ip` | Empty = detect (Tailscale addresses are skipped) |
| vMix Mix parameter offset | `mix_param_offset` | vMix's API `Mix` parameter = mix number + this. With `-1`, Mix 2 is sent as `Mix=1`. Check with **Show default on the projector**; if the wrong mix switches, try `0` |
| Download limit while live | `live_throttle_kbps` | KB/s while something is on the projector; `0` = no limit |

Command line, if you ever need it (as administrator): `vitrine-agent setup` (same as double-click), `install` (register the service from the exe's current folder), `uninstall`, `start`, `stop`, `run` (in the console, for testing), `version`. Log file: `C:\ProgramData\Vitrine\agent.log` (also under **Agent log** in the settings page).

## 3. Companion (tray icon → Open settings)

The companion is a page at `http://127.0.0.1:8090/companion`; the back tablet can open it too, without the Connection section and the log. It sets, for this PC:

- **Session**: which active service the tablets show.
- **Projector output**: the mix the tablets switch.
- **Default input**: chosen by number and name from vMix's live input list. The agent stores vMix's input key, so the right input is found even if numbers shift; if the banner changes, pick the new one here.
- **Transition** and duration for taking media, and separately the **transition back to the default** (e.g. when the default input has transparency and Fade looks like a cut: set one of vMix's transition buttons, *Transition 1–4*, to an effect that works for it and choose it here; Transition 1–4 and stingers use the duration set in vMix).
- **Go back to default when a video ends**, **jump mode** (crossfade between GM_A/GM_B, or jump in place), input names.
- **Tablets**: the paired list, unpair, and **Show pairing QR** (signed by the server when online, by the agent itself when the server is unreachable).

The tray icon turns red while guest media is live and grey when vMix or the agent isn't reachable. Only one tray icon runs per user; starting `vitrine-tray.exe` again does nothing. **Close tray icon** closes just the icon: the agent keeps running as a service.

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
