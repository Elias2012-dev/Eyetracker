# Eyetracker

Free head/eye tracking for your webcam — no TrackIR, no Tobii, no opentrack,
no FreeTrack. One tracker feeds every game:

| Target | How it connects |
|---|---|
| **Minecraft 26.2** (Fabric mod in this repo) | UDP JSON packets on `127.0.0.1:47777` |
| **Any TrackIR game** — ETS2/ATS, MSFS, DCS, X-Plane 12, War Thunder, ACC, DayZ, IL-2, … | **Built-in game link** — our own NPClient DLL + shared memory, registered automatically |
| **Any mouse-look game** (no TrackIR support required) | **Mouse emulation** — your head drives the cursor (`F9` arms/disarms) |
| **Anything opentrack supports** (optional extra) | opentrack "UDP over network" protocol on port `4242` |

```
                       ┌── UDP JSON :47777 ──────► Minecraft mod (camera control)
 webcam ─► tracker ────┼── game link ────────────► TrackIR games (our own NPClient.dll)
 (MediaPipe)           ├── mouse ────────────────► any mouse-look game (SendInput)
                       └── opentrack UDP :4242 ──► opentrack (optional)
```

The tracker estimates **head pose** (yaw / pitch / roll + translation) from a
single webcam — physical or a phone used as a Windows virtual camera — using
MediaPipe's face landmarker, smooths it with a One-Euro filter, applies your
calibration, and streams it out. Eyes-only gaze can be layered on later —
the pipeline and protocol already carry everything needed.

---

## Requirements

* **Windows** for the game link, the mouse output and the registry bridge.
  The tracker itself is cross-platform (OpenCV + MediaPipe).
* **Python 3.10–3.13** — `run.bat` creates `.venv` and installs
  `requirements.txt` (MediaPipe, OpenCV, NumPy) on first run.
* **A webcam**, or a phone acting as a Windows virtual camera (see
  [Using your phone as the camera](#using-your-phone-as-the-camera-windows-virtual-camera)).
* **The Minecraft mod is optional** and needs no Java of its own — Gradle
  downloads a JDK 25 for itself (foojay resolver).
* ~1 GB free for the virtualenv. The MediaPipe face-landmark model
  (~3.8 MB) is downloaded into `models/` on first run.

---

## Game support

One tracker, every game. `run.bat --list-games` shows the built-in presets;
`run.bat --game KEY` switches the outputs a game needs on (persisted) and
prints that game's setup notes.

| Game | `--game` key | How it connects |
|---|---|---|
| Minecraft 26.2 | `minecraft` | Fabric mod in this repo (UDP JSON) |
| Euro Truck Simulator 2 | `ets2` | built-in game link (TrackIR API) |
| American Truck Simulator | `ats` | built-in game link (TrackIR API) |
| Microsoft Flight Simulator 2024 / 2020 | `msfs` | built-in game link (TrackIR API) |
| DCS World | `dcs` | built-in game link (TrackIR API) |
| X-Plane 12 | `xplane` | built-in game link (*Settings → VR and Head Tracking → Enable TrackIR*) |
| War Thunder | `war-thunder` | built-in game link (TrackIR API) |
| IL-2 Sturmovik | `il2` | built-in game link (TrackIR API) |
| Assetto Corsa Competizione | `acc` | built-in game link (TrackIR API) |
| DayZ | `dayz` | built-in game link (TrackIR API) |
| **anything with mouse-look** | `mouse` | mouse emulation (head moves the cursor) |

**Any other TrackIR game works too** — our DLL *is* a normal NPClient: it
answers the same registry lookup, exports and checksum the TrackIR client
software would, so every title on the TrackIR supported list (780+ games)
can load it. Start the tracker first, enable head tracking in the game, done.

Games without TrackIR support are covered by **mouse emulation** — see
[Any other game — mouse emulation](#any-other-game--mouse-emulation).

---

## Quick start — Minecraft

1. **Start the tracker** (first run installs its Python dependencies):

   ```
   run.bat --list-cameras          # see every camera, incl. phone/virtual ones
   run.bat --camera-name "Camo"    # pick yours once (saved to eyetrack.json)
   run.bat
   ```

   The first run also fetches the MediaPipe face-landmark model into
   `models/` — no manual step.

   Press `C` once and walk through the 5-step calibration (look centre,
   left, right, up, down) — it takes 30 seconds and makes the feel right.

2. **Build the mod** (only needed once — no Java install needed: Gradle
   downloads the required JDK 25 itself):

   ```
   cd minecraft-mod
   gradlew build
   ```

   The jar lands in `minecraft-mod/build/libs/freebuff-eyetrack-1.0.0.jar`.

3. **Install**: put the jar plus [Fabric Loader](https://fabricmc.net/use/)
   (≥ 0.19) and Fabric API for 26.2 into your `mods` folder.

4. **In game**:

   | Key | Action |
   |---|---|
   | `H` | toggle head tracking on/off (rebindable in Controls) |
   | `J` | recenter — snap the current head pose to the current view |

   While tracking is on, the camera follows your head every rendered frame;
   turn it off (or lose the face) and mouse-look takes over instantly.

No camera handy? Test the plumbing with synthetic motion:

```
python tools/fake_tracker.py
```

## Using your phone as the camera (Windows virtual camera)

You don't need a webcam at all — any app that registers a **Windows virtual
camera** works, and the tracker can select it **by name**:

1. On the phone + PC install one of (all free tiers exist):
   **Camo**, **Iriun Webcam**, **DroidCam**, **EpocCam**, or use your phone
   maker's companion app (e.g. Motorola/Lenovo, Samsung, or "Link to
   Windows" streaming — devices show up as e.g. *"Elias A56 (Windows
   Virtuell Kamera)"*).
2. Start the companion app so the phone is **actually streaming** (a virtual
   camera that isn't fed outputs a black frame).
3. Enumerate and select it:

   ```
   run.bat --list-cameras
   run.bat --camera-name "Camo"        # case-insensitive substring, saved
   ```

   The name lives in `eyetrack.json` as `camera.device_name`; leave it empty
   to fall back to `camera.index`. Also check
   *Windows Settings → Bluetooth & devices → Cameras* to confirm the virtual
   camera exists.

Phone streams are often 720p30 — that's plenty; the tracker just asks for
`camera.width/height/fps` and takes what the device gives.

## Quick start — ETS2 / ATS (fully standalone)

Everything needed is already inside this repository: the tracker ships its
**own** 6.5 KB `NPClient.dll` (built from [bridge/src](bridge/src)) that the
games load, fed by the tracker's shared memory. No FreeTrack, no opentrack,
no extra software. Every TrackIR game in the table above is served exactly
this same way.

1. Start the tracker **before** launching the game:

   ```
   run.bat
   ```

   On first start it registers its bridge automatically (two HKCU values,
   previous values backed up to `bridge_registry_backup.json`). Manual
   control if you prefer:

   ```
   run.bat --install-bridge       # register once
   run.bat --uninstall-bridge     # restore previous registry values
   run.bat --no-auto-bridge       # never touch the registry automatically
   ```

2. In ETS2/ATS enable head tracking in *Options → Gameplay* — the game sees
   a normal TrackIR-style source. Recenter with `R` in the tracker overlay
   (or the game's own recenter key).

3. Axis directions/ranges are adjustable in-game; if you prefer configuring
   the tracker, flip `pose.invert_yaw` / `pose.invert_pitch` in
   `eyetrack.json`.

**Prefer opentrack instead?** It's optional:

```
run.bat --opentrack
```

then in opentrack set *Input → UDP over network* (port 4242) and pick any
output you like.

## Any other game — mouse emulation

Games without TrackIR support (most of the library) can be driven too: the
tracker **moves your mouse**.

```
run.bat --mouse          # enable it (saved to eyetrack.json)
run.bat --game mouse     # same, plus the per-game setup notes
```

* Yaw/pitch become *relative* cursor motion via Windows `SendInput` —
  exactly what a physical mouse does, no driver or extra software.
* It **starts disarmed** so your desktop stays usable: press **F9** to
  arm/disarm (`mouse.toggle_key`). The overlay status line always shows
  `mouse: ON/off [F9]`.
* Holding a head pose stops the cursor, returning to centre returns the
  cursor — no drift; sub-pixel motion is never rounded away.
* Speed and feel: `mouse.sensitivity` / `mouse.v_sensitivity` (pixels per
  degree, default `5`), `mouse.deadzone_deg` (default `2°` — swallows idle
  jitter), `mouse.invert_x` / `mouse.invert_y`.
* Windowed / borderless-fullscreen is the most reliable mode, and lowering
  the game's own mouse sensitivity tames fast games.

---

## Tracker keys (overlay window)

| Key | Action |
|---|---|
| `C` | start the calibration wizard (SPACE captures, ESC cancels) |
| `R` | recentre on your current neutral pose |
| `Q` / `ESC` | quit |
| `F9` | arm/disarm the mouse output (global; only when `mouse.enabled`) |

## Configuration files

**`eyetrack.json`** (tracker, created on first run):

| Key | Default | Meaning |
|---|---|---|
| `camera.index` | `0` | camera number (`run.bat --list-cameras` to probe) |
| `camera.device_name` | `""` | select camera by name substring, e.g. `"Camo"`, `"Iriun"`, `"OBS"` — for phone/virtual cameras |
| `camera.width/height/fps` | 1280×720@60 | capture request |
| `pose.yaw_gain` … `z_gain` | `1.0` | per-axis output gains |
| `pose.invert_*` | `false` | flip individual axes |
| `pose.reference_distance_cm` | `60` | seated distance used for depth (Z) |
| `filter.min_cutoff` / `beta` | `1.0` / `0.03` | One-Euro smoothing (lower = smoother, beta = speed response) |
| `udp_json.enabled/host/port` | `true` / `127.0.0.1` / `47777` | Minecraft stream |
| `game_link.enabled` | `true` on Windows | TrackIR game link (our NPClient DLL) |
| `game_link.auto_bridge` | `true` | auto-register the registry bridge at startup |
| `mouse.enabled` | `false` | mouse-emulation output for non-TrackIR games |
| `mouse.sensitivity` / `v_sensitivity` | `5.0` | mouse pixels per degree (yaw / pitch) |
| `mouse.deadzone_deg` | `2.0` | ignore head jitter around the neutral pose |
| `mouse.invert_x` / `mouse.invert_y` | `false` | flip the mouse axes |
| `mouse.toggle_key` | `"F9"` | arm/disarm key (`"none"` = always active) |
| `mouse.rate_hz` | `60` | mouse output rate |
| `opentrack_udp.enabled` | `false` | optional opentrack stream |
| `overlay.enabled` | `true` | preview window |

**`config/freebuff_eyetrack.json`** (Minecraft mod):

| Key | Default | Meaning |
|---|---|---|
| `port` / `bindAddress` | `47777` / `127.0.0.1` | UDP listener (`0.0.0.0` to track from another PC) |
| `yawSensitivity` / `pitchSensitivity` | `1.0` | degrees of view per degree of head |
| `yawRange` / `pitchRange` | `40` / `30` | normalisation range for the curve |
| `yawGamma` / `pitchGamma` | `1.0` | `<1` snappier near centre, `>1` calmer at the edges |
| `invertYaw` / `invertPitch` | `false` | flip axes |
| `staleTimeoutMs` | `500` | drop to "face lost" after this silence |

**`calibration.json`** — written by the wizard (scales + centre). Delete it
to return to heuristic defaults.

## Troubleshooting

* **Axes feel inverted** — tracker side: set `pose.invert_yaw`/`invert_pitch`;
  mod side: `invertYaw`/`invertPitch`; mouse: `mouse.invert_x`/`invert_y`;
  trucks: the game has its own axis inversion options.
* **Mouse doesn't move** — the mouse output starts *disarmed*: press `F9`
  (overlay shows `mouse: ON/off`). Then check `mouse.enabled` is `true`
  (`run.bat --mouse`) and that the game runs windowed/borderless.
* **Mouse too fast / too slow** — `mouse.sensitivity` and the game's own
  mouse sensitivity; `mouse.deadzone_deg` up stops idle drift at centre.
* **ETS2/ATS show no tracking** — start the tracker before the game; check
  the console for `[game-link]` lines (first run should say it registered
  the bridge). If you also use opentrack, *its* NPClient registration wins
  the registry key — either disable opentrack's output or run
  `run.bat --install-bridge` again after it.
* **Bridge DLLs missing** — run `bridge\build.bat` (TinyCC downloads
  automatically; prebuilt DLLs ship with the repo).
* **No face detected** — improve lighting (light *toward* your face), check
  the camera index (`--list-cameras`), sit roughly 50–70 cm away.
* **View drifts or jitters** — run the calibration wizard again (`C`), keep
  the camera centred on the monitor, raise `filter.min_cutoff` for less
  jitter or lower it for more smoothness.
* **Head movement feels too small/large in Minecraft** — `yawSensitivity`
  in the mod config, or re-run the wizard (its comfortable-turn captures set
  the mapping).
* **Firewall prompts** — everything defaults to loopback; only change
  `bindAddress`/`udp_json.host` if you deliberately track over LAN.

## Development

Repository layout:

| Path | What it is |
|---|---|
| `eyetrack/` | the tracker: capture → head pose → calibration → filter → outputs |
| `eyetrack/outputs/` | game links: shared memory (TrackIR), UDP JSON (Minecraft), opentrack UDP, mouse |
| `bridge/` | our own NPClient DLL: C sources, prebuilt DLLs, `NOTICE.txt` (ABI provenance) |
| `minecraft-mod/` | Fabric mod for Minecraft 26.2 |
| `tools/` | `fake_tracker.py` (synthetic motion), `inspect_pose.py` (pose debugging) |
| `tests/` | pytest suite: protocol, shared memory, DLL ABI roundtrip, mouse, presets |

```
# tracker tests (46 tests: filters, calibration, UDP formats, shared memory,
# mouse mapping, game presets, and a full writer -> bridge DLL roundtrip
# incl. ABI checksum verification)
.venv/Scripts/python -m pytest

# mod tests + build (3 protocol tests + compile against MC 26.2)
cd minecraft-mod && gradlew test build

# rebuild the bridge DLL after changing bridge/src
bridge\build.bat
```

Protocol notes:

* **Minecraft stream** — UTF-8 JSON datagram, one object per packet:
  `{"v":1,"t":…,"seq":n,"tracking":bool,"yaw":…,"pitch":…,"roll":…,"x":…,"y":…,"z":…}`
  (angles degrees, yaw + = turned to your left, pitch + = looking up;
  translations cm relative to the calibrated centre).
* **Game link** — 56-byte `EBT_GameLink_v1` shared-memory block (layout in
  [eyetrack/outputs/game_link.py](eyetrack/outputs/game_link.py), mirrored by
  [bridge/src/ebt_npclient.c](bridge/src/ebt_npclient.c)); the DLL converts
  to the TrackIR client ABI (angles ±16383 over ±180°, translation ±16383
  over ±50 cm, standard checksum) inside the game process.
* **opentrack UDP** — six little-endian `float64`: `x, y, z, yaw, pitch, roll`
  (cm, degrees), the format of opentrack's *UDP over network* input.

Toolchain: Python 3.10–3.13 with MediaPipe 1.0 (Tasks API), OpenCV, NumPy;
mod targets Fabric Loader 0.19.5 / Fabric API 0.161.0+26.2 / Loom 1.18 /
Gradle 9.7 / Java 25 (auto-provisioned into `.jdk/`); bridge DLLs are plain
C built with TinyCC (`bridge/tools`).

## Credits & licensing

* Tracker, mod and bridge source: MIT (see [LICENSE](LICENSE)).
* The MediaPipe face-landmark model (`models/face_landmarker.task`, fetched
  at runtime from Google's model repository) is Apache-2.0, © Google.
* `bridge/src/ebt_npclient.c` implements the published TrackIR client API
  (exports, data layout, checksum, signature constants) as established by
  the public reference clients (wine / linuxtrack) — see
  `bridge/NOTICE.txt` for details.
* FreeTrack 2.0 protocol by the FreeTrack team informed the earlier design;
  this project no longer uses any FreeTrack binaries or shared memory.
* Thanks to the openTrack and linuxtrack projects for keeping head tracking
  free and open.
