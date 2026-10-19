"""Weighted public order flow.

Accumulates trades from the public tape, weighted per account, with
exponential decay. Weights come from `analysis.account_scoring` run over
resolved history: accounts with a durable edge get positive weight, accounts
that are reliably wrong (or that systematically take the other side of a
followed account) get negative weight. Unknown accounts are ignored.
"""

from __future__ import annotations

from math import exp, tanh

from ..config import OrderFlowConfig
from ..models import PublicTrade, SignalOutput
from .base import SignalContext


class OrderFlowSignal:
    name = "order_flow"

    def __init__(self, cfg: OrderFlowConfig) -> None:
        self.cfg = cfg
        self.weights: dict[str, float] = dict(cfg.account_weights)
        self._flow: dict[str, tuple[float, float]] = {}  # market_id -> (value, as_of_ts)

    def set_weights(self, weights: dict[str, float]) -> None:
        self.weights = dict(weights)

    def _decayed(self, market_id: str, now: float) -> float:
        val, ts = self._flow.get(market_id, (0.0, now))
        return val * exp(-(now - ts) / self.cfg.decay_s) if now > ts else val

    def on_trade(self, ev: PublicTrade) -> None:
        w = self.weights.get(ev.account)
        if not w:
            return
        val = self._decayed(ev.market_id, ev.ts) + w * ev.side.sign * ev.size
        self._flow[ev.market_id] = (val, ev.ts)

    def forget(self, market_id: str) -> None:
        self._flow.pop(market_id, None)

    def evaluate(self, ctx: SignalContext) -> SignalOutput:
        val = self._decayed(ctx.view.market.market_id, ctx.now)
        if abs(val) < self.cfg.min_abs_flow:
            return SignalOutput.neutral(self.name, "no meaningful flow")
        return SignalOutput(self.name, tanh(val / self.cfg.flow_scale), 1.0, reason=f"flow={val:.0f}")
