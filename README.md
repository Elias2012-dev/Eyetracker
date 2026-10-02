# Eyetracker

[![Downloads](https://img.shields.io/github/downloads/Elias2012-dev/Eyetracker/total?style=flat-square&label=downloads)](https://github.com/Elias2012-dev/Eyetracker/releases)
[![CI](https://github.com/Elias2012-dev/Eyetracker/actions/workflows/ci.yml/badge.svg)](https://github.com/Elias2012-dev/Eyetracker/actions/workflows/ci.yml)

Free head/eye tracking for your webcam — no TrackIR, no Tobii, no FreeTrack,
no extra software. One tracker feeds every game:

| Target | How it connects |
|---|---|
| **Minecraft 26.2** (Fabric mod in this repo) | UDP JSON packets on `127.0.0.1:47777` |
| **Any TrackIR game** — ETS2/ATS, MSFS, DCS, X-Plane 12, War Thunder, ACC, DayZ, IL-2, FS22/FS25, … | **Built-in game link** — our own NPClient DLL + shared memory, registered automatically |
| **Any mouse-look game** (no TrackIR support required) | **Mouse emulation** — your head drives the cursor (`F9` arms/disarms) |

```
                       ┌── UDP JSON :47777 ──────► Minecraft mod (camera control)
 webcam ─► tracker ────┼── game link ────────────► TrackIR games (our own NPClient.dll)
 (MediaPipe)           └── mouse ────────────────► any mouse-look game (SendInput)
```

The tracker estimates **head pose** (yaw / pitch / roll + translation) from a
single webcam — physical or a phone used as a Windows virtual camera — using
MediaPipe's face landmarker, smooths it with a One-Euro filter, applies your
calibration, and streams it out. Eyes-only gaze can be layered on later —
the pipeline and protocol already carry everything needed.

---

## Installation

New here? Follow **[INSTALL.md](INSTALL.md)** — step-by-step setup for the
exe, the Minecraft mod, a phone as the camera, running from source, and a
verification checklist.

---

## Download — get `Eyetracker.exe`

**How to download: go to the releases page and install `Eyetracker.exe`.**

👉 **[github.com/Elias2012-dev/Eyetracker/releases](https://github.com/Elias2012-dev/Eyetracker/releases)**

| File | What it is |
|---|---|
| [**Eyetracker.exe**](https://github.com/Elias2012-dev/Eyetracker/releases/latest/download/Eyetracker.exe) | the whole tracker, self-contained (~113 MB) — no Python, no terminal, no pip |
| [**freebuff-eyetrack-1.0.0.jar**](https://github.com/Elias2012-dev/Eyetracker/releases/latest/download/freebuff-eyetrack-1.0.0.jar) | the Minecraft 26.2 mod (only needed for Minecraft) |

**Installing is the download.** No installer, no setup wizard, no
dependencies:

1. Download `Eyetracker.exe` from the releases page.
2. Double-click it. The preview window that opens *is* the interface.
3. Start it **before** the game.
   * **ETS2 / ATS** — *Options → Gameplay → TrackIR* on.
   * **MSFS / DCS / X-Plane 12 / War Thunder / ACC / DayZ / IL-2** — enable
     head tracking in the game; it loads our DLL straight from the standard
     TrackIR registry key.
   * **Minecraft** — drop `freebuff-eyetrack-1.0.0.jar` into your `mods`
     folder next to Fabric Loader + Fabric API, then press `H` to toggle
     tracking and `J` to recentre.
   * **Any mouse-look game** — run with `--mouse` and press `F9` to arm.

Windows may say "Windows protected your PC" because the build is not
code-signed: choose **More info → Run anyway**. Every build is reproducible
from the source in this repository, so you can verify or rebuild it
yourself ([build_exe.bat](#run-it-as-a-standalone-exe)).

Prefer a phone or no download? See
[Using your phone as the camera](#using-your-phone-as-the-camera-windows-virtual-camera).

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

**Nothing else to install.** No TrackIR hardware, no FreeTrack, no virtual
device, no driver — every target above is served by code in this
repository, and all three outputs live in it too. There is no third-party
tracker to install, configure or keep up to date.

---

## Run it as a standalone .exe

You do not have to build this — **download `Eyetracker.exe` from the
[releases page](#download-get-eyetrackerexe)**. To build it yourself (for
example to verify the published binary):

```
build_exe.bat              -> dist\Eyetracker.exe      (one file, ~113 MB)
build_exe.bat --onedir     -> dist\Eyetracker\...     (folder, instant start)
```

The script creates the virtualenv, installs PyInstaller and bundles
everything the tracker needs: the MediaPipe model (so the first launch
works offline), the bridge DLLs, and camera-by-name support.

Double-click `Eyetracker.exe` and the preview window opens — that window is
the whole UI. Everything the app writes goes to `%APPDATA%\Eyetracker`:

| File | What it is |
|---|---|
| `Eyetracker.log` | every line the tracker prints, always logged |
| `eyetrack.json` | your config (created on first run) |
| `calibration.json` | calibration result |
| `bridge\NPClient*.dll` | staged for the games — a one-file build unpacks into a temp folder that is deleted on exit, so the DLLs are copied somewhere permanent first |

Options still work when you launch it from a terminal or script; with no
console the same text appears in a dialog (set `EYE_TRACKER_NO_DIALOG=1`
to suppress that for unattended runs):

```
Eyetracker.exe --pick-camera     # choose from a list of every detected camera
Eyetracker.exe --list-cameras    # dialog listing your cameras
Eyetracker.exe --list-games      # the game preset matrix
Eyetracker.exe --paths           # where config, model, DLLs and log live
Eyetracker.exe --mouse           # head-to-cursor output
Eyetracker.exe --game ets2       # apply a preset + print its setup notes
```

Good to know:

* The first launch of the one-file build takes a few seconds (it unpacks
  itself); `--onedir` starts instantly if that bothers you.
* The registered game-link path follows **how you launch**: the exe
  registers `%APPDATA%\Eyetracker\bridge`, `run.bat` registers the
  repository's `bridge\`. Both work — whichever you used last is active,
  and `Eyetracker.exe --install-bridge` / `run.bat --install-bridge` switch.
* Nothing changes for running from source: same code, same config format.

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
| Farming Simulator 25 | `fs25` | built-in game link (TrackIR API) |
| Farming Simulator 22 | `fs22` | built-in game link (TrackIR API) |
| **anything with mouse-look** | `mouse` | mouse emulation (head moves the cursor) |

**Farming Simulator (22 / 25)** works like ETS2 — both run on the GIANTS
engine, which loads a TrackIR client DLL the same way. Enable head
tracking under *Options → General*. If the view doesn't move, open the
game's `log.txt` and find the `Head Tracking System` line: it names the
system the engine actually selected. `TrackIR Client` means the engine saw
our bridge and the problem is in-game settings; `none` means it didn't find
it, so check the tracker was started first.

Our bridge DLLs are 64-bit only, which covers FS25 (64-bit only) and every
other game in the table above. A genuinely 32-bit game won't load them —
see [bridge/NOTICE.txt](bridge/NOTICE.txt).

**Any other TrackIR game works too** — our DLL *is* a normal NPClient: it
answers the same registry lookup, exports and checksum the TrackIR client
software would, so every title on the TrackIR supported list (780+ games)
can load it. Start the tracker first, enable head tracking in the game, done.

Games without TrackIR support are covered by **mouse emulation** — see
[Any other game — mouse emulation](#any-other-game-mouse-emulation).

---

## Quick start — Minecraft

1. **Start the tracker** (first run installs its Python dependencies):

   ```
   run.bat --pick-camera           # choose from a list of every detected camera
   run.bat --list-cameras          # see every camera, incl. phone/virtual ones
   run.bat --camera-name "Camo"    # or pick by name (saved to eyetrack.json)
   run.bat
   ```

   `--pick-camera` lists every camera Windows reports and takes either a
   number or any part of a name, then saves the choice — so you only have
   to do it once. It stores the *device name* rather than the index,
   because indices shuffle when you unplug a webcam or reboot, and the
   name doesn't.

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
   Windows" streaming — devices show up as e.g. *"Pixel 8 (Windows
   Virtuell Kamera)"*, named after your phone).
2. Start the companion app so the phone is **actually streaming** (a virtual
   camera that isn't fed outputs a black frame).
3. Enumerate and select it:

   ```
   run.bat --pick-camera                # pick from the list, saved for next time
   run.bat --list-cameras               # or just look
   run.bat --camera-name "Camo"         # case-insensitive substring, saved
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
games load, fed by the tracker's shared memory. No FreeTrack, no extra
software. Every TrackIR game in the table above is served exactly
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

### The HUD

The preview window *is* the interface, so it answers three things at a
glance: a status pill for whether a face is found, three centre-anchored
meters with an explicit sign legend (`yaw +12.3°  + = your left`), and the
calibration wizard when it is running. The sign legends are the point —
which way is "positive" is the number one source of "the camera moves the
wrong way" confusion.

Two layouts:

| Flag | Effect |
|---|---|
| *(default)* | full camera view with the HUD on top |
| `--compact` | small numbers-only panel, no camera image |
| `--top-most` | keep the HUD above the game window |

`--compact` and `--top-most` are the pair to reach for when a game has the
focus: a 460×250 panel is easy to keep in peripheral vision. Both persist
to the config, so `Eyetracker.exe --compact` needs saying only once. Both
have `--no-` inverses, which is what lets a one-off flag *not* overwrite a
saved preference.

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
| `overlay.enabled` | `true` | preview window |
| `overlay.compact` | `false` | numbers-only HUD instead of the camera view |
| `overlay.top_most` | `false` | keep the HUD above other windows |

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
  the bridge). If *other* tracker software installed on this machine owns
  the NPClient registry key, re-run `run.bat --install-bridge` after it
  starts, or disable that software while playing.
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
| `eyetrack/outputs/` | game links: shared memory (TrackIR), UDP JSON (Minecraft), mouse |
| `packaging/` | PyInstaller spec + launcher for the standalone exe |
| `bridge/` | our own NPClient DLL: C sources, prebuilt DLLs, `NOTICE.txt` (ABI provenance) |
| `minecraft-mod/` | Fabric mod for Minecraft 26.2 |
| `tools/` | `fake_tracker.py` (synthetic motion), `inspect_pose.py` (pose debugging) |
| `tests/` | pytest suite: protocol, shared memory, DLL ABI roundtrip, mouse, presets |

```
# tracker tests (115 tests: filters, calibration, UDP formats, shared memory,
# mouse mapping, camera selection, HUD rendering, game presets, packaging
# paths, zero-external-tracker guarantees, and a full writer -> bridge DLL
# roundtrip incl. ABI checksum verification)
.venv/Scripts/python -m pytest

# mod tests + build (3 protocol tests + compile against MC 26.2)
cd minecraft-mod && gradlew test build

# rebuild the bridge DLL after changing bridge/src
bridge\build.bat

# build the standalone exe (see packaging/eyetracker.spec)
build_exe.bat
```

Both suites run automatically on GitHub Actions for every push to `main`
and every pull request — see the badge at the top. The workflow
(`.github/workflows/ci.yml`) runs on Windows for both jobs: the tracker's
shared-memory, registry and DLL tests skip themselves elsewhere, and the
mod needs JDK 25 to compile.

### Cutting a release

```bash
python tools/release.py 1.4.0              # check, tag, push
python tools/release.py 1.4.0 --dry-run    # checks only, changes nothing
python tools/release.py 1.4.0 --build      # ...and build + smoke-test locally
```

The script **refuses to run on a dirty tree** — a release must be
reproducible from its tag, and uncommitted changes are not in the tag. It
also fails early on a malformed version, a tag that already exists, unpushed
commits, a missing face model, and missing bridge DLLs. With `--build` it
reproduces the exe and jar locally first, so a broken packaging change
surfaces on your machine instead of in the Actions log after the tag is
already public.

It then pushes the tag, and the Release workflow builds both artifacts on a
Windows runner and attaches them to the GitHub Release:

```bash
git tag -a v1.3.0 -m "v1.3.0" && git push origin v1.3.0   # what the script does
```

The checkout is pinned to the tag, so the published assets always come from
the commit the tag names. Before uploading, the workflow smoke-tests the exe
(it must unpack, import, and actually contain the bundled face model), and
asserts the jar is non-empty. Re-running a tag is safe — assets are
clobbered rather than duplicated. You can also trigger it by hand from the
Actions tab with an existing tag.

`models/*.task` is gitignored, so a clean runner has no face model; the
build fetches it from the same URL the app uses at runtime, and the
PyInstaller spec now aborts rather than quietly shipping an exe without it.

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
* **Mouse emulation** — no protocol: head deltas become relative
  `SendInput` cursor motion in the game process itself.

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
* Thanks to the linuxtrack project for keeping the TrackIR client ABI
  documented and implementable in the open.
