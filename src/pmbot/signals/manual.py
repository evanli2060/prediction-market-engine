"""Operator override.

Scheduled macro events move the underlying faster than any of the automated
signals can react. During those windows an operator enters a directional bias
(+1 / -1) with a short time-to-live; it expires on its own so a forgotten
override can't keep trading.
"""

from __future__ import annotations

from ..config import ManualConfig
from ..models import OperatorSignal, SignalOutput
from ..pricing import clamp
from .base import SignalContext


class ManualOverrideSignal:
    name = "manual"

    def __init__(self, cfg: ManualConfig) -> None:
        self.cfg = cfg
        self._value = 0.0
        self._until = float("-inf")
        self._note = ""

    def on_operator(self, ev: OperatorSignal) -> None:
        self._value = clamp(ev.value, -1.0, 1.0)
        self._until = ev.ts + max(0.0, ev.ttl_s)
        self._note = ev.note

    def active(self, now: float) -> bool:
        return now < self._until and self._value != 0.0

    def evaluate(self, ctx: SignalContext) -> SignalOutput:
        if not self.active(ctx.now):
            return SignalOutput.neutral(self.name)
        if self.cfg.applies_to and ctx.view.market.underlying not in self.cfg.applies_to:
            return SignalOutput.neutral(self.name, "out of scope")
        return SignalOutput(self.name, self._value, 1.0, reason=self._note or "operator")
