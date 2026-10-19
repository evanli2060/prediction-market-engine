from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import SignalOutput
from ..state import MarketView, WorldState


@dataclass(frozen=True)
class SignalContext:
    now: float
    view: MarketView
    world: WorldState


class Signal(Protocol):
    """A signal turns the current world into a directional opinion on one market.

    Contract: pure function of `ctx` plus whatever the signal has accumulated from
    events it subscribed to. Must be cheap: it runs on every book update.
    """

    name: str

    def evaluate(self, ctx: SignalContext) -> SignalOutput: ...
