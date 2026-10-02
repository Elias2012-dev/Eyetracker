"""Output sinks: where the calibrated pose goes."""

from __future__ import annotations

from ..config import Config
from .game_link import GameLinkOutput
from .mouse import MouseOutput
from .udp_json import UdpJsonOutput

__all__ = ["GameLinkOutput", "MouseOutput", "UdpJsonOutput", "build_outputs"]


def build_outputs(cfg: Config) -> list:
    """Instantiate every output enabled in the config, in a stable order.

    Three sinks, all self-contained: the TrackIR game link, the Minecraft
    UDP stream and mouse emulation. There is no "hand it to another
    tracker" path - the repository is the whole installation.
    """
    outputs: list = []
    if cfg.udp_json.enabled:
        outputs.append(UdpJsonOutput(cfg.udp_json.host, cfg.udp_json.port, cfg.udp_json.rate_hz))
    if cfg.game_link.enabled:
        outputs.append(GameLinkOutput())
    if cfg.mouse.enabled:
        outputs.append(MouseOutput(cfg.mouse))
    return outputs