"""Built-in per-game profiles: pick a game, get the right outputs + setup notes.

The heavy lifting is done elsewhere:

* **TrackIR-native games** are served by our own ``bridge/NPClient.dll`` -
  any game that looks up the NaturalPoint registry key loads it, so the whole
  TrackIR catalogue works without any TrackIR software installed.  This
  includes the GIANTS-engine sims (Farming Simulator 22/25), which probe for
  a TrackIR client the same way ETS2/ATS do.
* **Minecraft 26.2** uses the Fabric mod in this repo (UDP JSON stream).
* **Everything else** can be driven through the mouse-emulation output
  (head pose -> relative cursor motion).

A preset just makes sure the outputs a game needs are enabled and prints the
per-game setup notes.  Apply one with ``--game KEY``; list them with
``--list-games``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config

# Displayed by --list-games.
CONNECTION = {
    "trackir": "built-in game link (TrackIR API)",
    "mod": "Minecraft Fabric mod (UDP JSON :47777)",
    "mouse": "mouse emulation (moves your cursor)",
}

_TRACKIR_COMMON = (
    "Start the tracker BEFORE the game - it registers the NPClient bridge on its own.",
    "The game loads our own bridge/NPClient.dll: no TrackIR or FreeTrack install needed.",
    "Recenter with R in the tracker overlay (or the game's own recenter key).",
)


@dataclass(frozen=True)
class GamePreset:
    key: str
    title: str
    family: str                      # one of CONNECTION
    outputs: tuple[str, ...]         # Config sections to switch on
    notes: tuple[str, ...] = field(default_factory=tuple)

    def apply(self, cfg: Config) -> bool:
        """Enable this preset's outputs; returns True if anything changed."""
        changed = False
        for name in self.outputs:
            section = getattr(cfg, name)
            if not section.enabled:
                section.enabled = True
                changed = True
        return changed


def _trackir(key: str, title: str, *extra: str) -> GamePreset:
    return GamePreset(key=key, title=title, family="trackir",
                      outputs=("game_link",),
                      notes=_TRACKIR_COMMON + tuple(extra))


PRESETS: dict[str, GamePreset] = {p.key: p for p in (
    GamePreset(
        key="minecraft",
        title="Minecraft 26.2 (Fabric mod)",
        family="mod",
        outputs=("udp_json",),
        notes=(
            "Build the mod once: cd minecraft-mod && gradlew build",
            "In game: H toggles head tracking, J recentres.",
            "Sensitivity / curve / invert live in config/freebuff_eyetrack.json.",
        ),
    ),
    _trackir("ets2", "Euro Truck Simulator 2",
             "In game: Options -> Gameplay -> enable TrackIR head tracking.",
             "Axis direction/range are adjustable in-game."),
    _trackir("ats", "American Truck Simulator",
             "In game: Options -> Gameplay -> enable TrackIR head tracking.",
             "Axis direction/range are adjustable in-game."),
    _trackir("msfs", "Microsoft Flight Simulator 2024 / 2020",
             "Sim supports TrackIR natively - enable head tracking in the camera/settings options."),
    _trackir("dcs", "DCS World",
             "DCS supports TrackIR natively - enable it in the game's options."),
    _trackir("xplane", "X-Plane 12",
             "In game: Settings -> VR and Head Tracking -> Enable TrackIR."),
    _trackir("war-thunder", "War Thunder",
             "War Thunder supports TrackIR natively - enable it in the game options."),
    _trackir("il2", "IL-2 Sturmovik",
             "The series supports TrackIR natively - enable it in the game options."),
    _trackir("acc", "Assetto Corsa Competizione",
             "ACC supports TrackIR natively - enable it in the controls/camera options."),
    _trackir("dayz", "DayZ",
             "DayZ supports TrackIR natively - enable it in the game settings."),
    _trackir("fs25", "Farming Simulator 25",
             "In game: Options -> General -> enable head tracking (TrackIR).",
             'GIANTS games read game.xml: <headTracking active="true" trackir="true"/>.',
             "Not moving? Open log.txt and find the 'Head Tracking System' line -",
             "it names the system the game picked. 'TrackIR Client' means it saw",
             "our bridge and something else is wrong; 'none' means it did not."),
    _trackir("fs22", "Farming Simulator 22",
             "In game: Options -> General -> enable head tracking (TrackIR).",
             "Same GIANTS engine as FS25, so the same log.txt check applies.",
             "If log.txt says 'none' on a 32-bit install, our shipped DLLs are",
             "64-bit only - see bridge/NOTICE.txt."),
    GamePreset(
        key="mouse",
        title="Any mouse-look game (no TrackIR needed)",
        family="mouse",
        outputs=("mouse",),
        notes=(
            "Press F9 to arm/disarm cursor control - it starts DISARMED (mouse.toggle_key).",
            "Head turns now move the mouse: yaw looks left/right, pitch looks up/down.",
            "Lower the game's own mouse sensitivity if it feels too fast.",
            "Windowed / borderless-fullscreen is the most reliable mode.",
            "Tune mouse.sensitivity / mouse.deadzone_deg / mouse.invert_* in eyetrack.json.",
        ),
    ),
)}


def apply_preset(key: str, cfg: Config) -> GamePreset:
    """Enable the outputs a game needs; raises SystemExit on a bad key."""
    preset = PRESETS.get(key.strip().lower())
    if preset is None:
        raise SystemExit(
            f"Unknown game preset {key!r}. Available: " + ", ".join(PRESETS)
        )
    preset.apply(cfg)
    return preset


def print_catalog() -> None:
    """``--list-games``: the built-in game support matrix."""
    print("Built-in game support - apply one with: run.bat --game KEY\n")
    kw = max(len(k) for k in PRESETS)
    tw = max(len(p.title) for p in PRESETS.values())
    print(f"  {'KEY':<{kw}}  {'GAME':<{tw}}  HOW IT CONNECTS")
    for key, p in PRESETS.items():
        print(f"  {key:<{kw}}  {p.title:<{tw}}  {CONNECTION[p.family]}")
    print("\nAny other TrackIR game works too - our DLL is a normal NPClient,")
    print("so every game on the TrackIR supported list can load it.")
    print("Per-game setup notes are printed when you pass --game KEY.")
