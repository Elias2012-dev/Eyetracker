"""Built-in game presets: catalogue integrity, application, CLI surface."""

import pytest

from eyetrack import presets as P
from eyetrack.__main__ import main
from eyetrack.config import Config


def test_catalog_is_well_formed():
    assert P.PRESETS, "catalog must not be empty"
    for key, preset in P.PRESETS.items():
        assert preset.key == key
        assert preset.family in P.CONNECTION
        assert preset.family in ("trackir", "mod", "mouse")
        assert preset.notes, f"{key} must ship setup notes"
        assert preset.outputs, f"{key} must enable at least one output"
        # every referenced output must be a real Config section with a switch
        for name in preset.outputs:
            section = getattr(Config(), name)
            assert hasattr(section, "enabled"), f"config.{name} has no .enabled"


def test_expected_games_are_covered():
    for key in ("minecraft", "ets2", "ats", "msfs", "dcs", "xplane",
                "war-thunder", "il2", "acc", "dayz", "mouse"):
        assert key in P.PRESETS, f"missing preset: {key}"


def test_family_determines_the_connection():
    for key, preset in P.PRESETS.items():
        if preset.family == "trackir":
            assert preset.outputs == ("game_link",), key
        elif preset.family == "mod":
            assert preset.outputs == ("udp_json",), key
        else:
            assert preset.outputs == ("mouse",), key


def test_apply_enables_outputs():
    cfg = Config()
    cfg.mouse.enabled = False
    preset = P.apply_preset("mouse", cfg)
    assert cfg.mouse.enabled is True
    assert preset.title.startswith("Any mouse-look game")


def test_apply_is_idempotent():
    cfg = Config()
    before = cfg.game_link.enabled
    P.apply_preset("ets2", cfg)
    assert cfg.game_link.enabled is True
    P.apply_preset("ets2", cfg)                 # second run: no error
    assert cfg.game_link.enabled is True
    assert before in (True, False)


def test_apply_rejects_unknown_games():
    with pytest.raises(SystemExit) as exc:
        P.apply_preset("quake", Config())
    assert "ets2" in str(exc.value)             # the error lists valid keys


def test_list_games_prints_the_matrix(capsys):
    assert main(["--list-games"]) == 0
    out = capsys.readouterr().out
    for needle in ("ets2", "Euro Truck Simulator 2", "mouse", "--game KEY"):
        assert needle in out, f"--list-games must mention {needle!r}"


def test_print_catalog_shows_every_preset(capsys):
    P.print_catalog()
    out = capsys.readouterr().out
    for key in P.PRESETS:
        assert key in out
