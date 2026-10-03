# Using the Eyetracker Minecraft mod

Short version: **start the tracker, load into a world, and press `H`.**

Everything else on this page is detail.

---

## You must press `H` in the game

This is the step people miss, and it makes a perfectly working setup look
broken.

The mod loads and waits. It does **not** start head tracking by itself.
Until you press `H`, Minecraft behaves completely normally and the camera
never moves — even though Eyetracker is running and clearly seeing you.

| What you did | What happens |
|---|---|
| Started `Eyetracker.exe`, it shows `TRACKING` | nothing yet |
| Loaded into a Minecraft world | nothing yet |
| **Pressed `H`** | head tracking turns on |
| Press `J` | recentres on how you sit right now |

Press `H` again to turn it back off.

## The line that tells you what is happening

Join a world and the mod prints one line:

```
[EyeTrack] Head tracking OFF (tracker found on UDP 47777). Press H to toggle, J to recentre.
```

Read it like this:

| You see | Meaning |
|---|---|
| `Head tracking OFF (tracker found on UDP 47777)` | all good — Eyetracker is running. Press `H`. |
| `Head tracking ON (...)` | already on. |
| `No tracker data yet on UDP 47777` | start `Eyetracker.exe` and press `C` to calibrate, then press `H`. |

If pressing `H` says *"ON, but no tracker data"* then the tracker is not
sending — start it and calibrate first.

## Setup

![mod icon](minecraft-mod/src/main/resources/assets/eyetrack/icon.png)

1. Install **Fabric Loader** and **Fabric API** for Minecraft 26.2.
2. Put `eyetrack-1.0.0.jar` in your `mods` folder:

   | Platform | Path |
   |---|---|
   | Windows | `%appdata%\.minecraft\mods` |
   | macOS | `~/Library/Application Support/minecraft/mods` |
   | Linux | `~/.minecraft\mods` |

3. Start `Eyetracker.exe` **before** Minecraft. On the first run it
   calibrates itself: follow the prompts and hold each pose still, and each
   step captures on its own. Press `ESC` to skip and `C` later to redo it.
4. Load into a world and press **`H`**.

## Keys

| Key | Action |
|---|---|
| `H` | head tracking on/off — **you must press this** |
| `J` | recentre to how you sit right now |

Both are rebindable under *Options → Controls*.

## Tuning

Edit `config/eyetrack.json` in your Minecraft folder (created on first run):

| Key | Default | Meaning |
|---|---|---|
| `yawSensitivity` / `pitchSensitivity` | `1.0` | degrees of view per degree of head |
| `yawRange` / `pitchRange` | `40` / `30` | normalisation range for the curve |
| `yawGamma` / `pitchGamma` | `1.0` | `<1` snappier near centre, `>1` calmer at the edges |
| `invertYaw` / `invertPitch` | `false` | flip axes |
| `staleTimeoutMs` | `500` | drop to "face lost" after this silence |
| `port` | `47777` | UDP port the tracker streams to — must match Eyetracker |

## Troubleshooting

**Nothing happens, no message at all.** The mod is not loaded. Check the
mod list on the Minecraft title screen, and confirm you are on Fabric with
Fabric API installed.

**Message says `No tracker data yet`.** Start `Eyetracker.exe`, wait for it
to show `TRACKING`, then press `H` again.

**Camera drifts or does not return to centre.** Press `J`, and check the
windows are behind the webcam, not beside it.

**Whole view shakes.** Lower `yawGamma`/`pitchGamma` toward `1.0`, or raise
`staleTimeoutMs`.

Full guide: [INSTALL.md](INSTALL.md)

## Publishing the mod

Two icons, because the platforms disagree:

| File | Use | Why |
|---|---|---|
| [`docs/mod-icon-512.png`](docs/mod-icon-512.png) | **upload this to Modrinth and CurseForge** | CurseForge's submission guide requires "at least minimum 400*400 px" and will not scale up; Modrinth scales down for you |
| [`minecraft-mod/src/main/resources/assets/eyetrack/icon.png`](minecraft-mod/src/main/resources/assets/eyetrack/icon.png) | ships inside the jar | what Fabric shows in the mod list; 128x128 is conventional and keeps the jar small |

Both are the same drawing at two resolutions, generated from one description
so they cannot drift apart. Regenerate with `python tools/make_mod_icon.py`;
`tests/test_icon.py` checks both sizes, plus centring, symmetry, contrast
and palette.

![mod icon](docs/mod-icon-512.png)