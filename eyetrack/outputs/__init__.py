"""Output sinks: where the calibrated pose goes."""

from __future__ import annotations

from ..config import Config
from .game_link import GameLinkOutput
from .opentrack_udp import OpentrackUdpOutput
from .udp_json import UdpJsonOutput

__all__ = ["GameLinkOutput", "OpentrackUdpOutput", "UdpJsonOutput", "build_outputs"]


def build_outputs(cfg: Config) -> list:
    """Instantiate every output enabled in the config, in a stable order."""
    outputs: list = []
    if cfg.udp_json.enabled:
        outputs.append(UdpJsonOutput(cfg.udp_json.host, cfg.udp_json.port, cfg.udp_json.rate_hz))
    if cfg.game_link.enabled:
        outputs.append(GameLinkOutput())
    if cfg.opentrack_udp.enabled:
        outputs.append(OpentrackUdpOutput(cfg.opentrack_udp.host, cfg.opentrack_udp.port,
                                          cfg.opentrack_udp.rate_hz))
    return outputs
