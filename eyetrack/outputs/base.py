"""Base class for pose outputs."""

from __future__ import annotations

from typing import Protocol


class Output(Protocol):
    name: str

    def start(self) -> None: ...

    def send(self, pose: dict[str, float], tracking: bool, t: float) -> None: ...

    def close(self) -> None: ...


class BaseOutput:
    name = "base"

    def start(self) -> None:
        pass

    def send(self, pose: dict[str, float], tracking: bool, t: float) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass
