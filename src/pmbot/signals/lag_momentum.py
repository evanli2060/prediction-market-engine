"""Underlying price vs. a lagging prediction-market book.

The book on a short-duration market reprices off the underlying, but not
instantly. Between the underlying moving and the book catching up there is a
window where a fair value computed from the live underlying disagrees with the
quoted price. This signal measures that gap, tilted by recent momentum.
"""

from __future__ import annotations

from math import log

from ..config import LagMomentumConfig
from ..models import SignalOutput
from ..pricing import binary_fair_prob
from .base import SignalContext


class LagMomentumSignal:
    name = "lag_momentum"

    def __init__(self, cfg: LagMomentumConfig) -> None:
        self.cfg = cfg

    def fair_prob(self, ctx: SignalContext) -> float | None:
        m = ctx.view.market
        u = ctx.world.underlyings.get(m.underlying)
        if u is None or u.last_price is None:
            return None
        vol = u.vol_per_s()
        if vol is None:
            return None
        spot = u.last_price
        t_left = m.time_left(ctx.now)

        expected_move = 0.0
        past = u.price_ago(ctx.now, self.cfg.lookback_s)
        if past:
            drift_per_s = self.cfg.momentum_weight * log(spot / past) / self.cfg.lookback_s
            expected_move = drift_per_s * min(t_left, self.cfg.momentum_horizon_s)

        return binary_fair_prob(spot, m.strike, vol, t_left, expected_move)

    def evaluate(self, ctx: SignalContext) -> SignalOutput:
        book = ctx.view.book
        u = ctx.world.underlyings.get(ctx.view.market.underlying)
        if book is None or u is None or u.last_ts is None:
            return SignalOutput.neutral(self.name, "no data")
        if ctx.now - u.last_ts > self.cfg.max_tick_age_s:
            return SignalOutput.neutral(self.name, "underlying feed stale")

        fair = self.fair_prob(ctx)
        if fair is None:
            return SignalOutput.neutral(self.name, "warming up")

        yes_edge = fair - book.best_ask
        no_edge = book.best_bid - fair  # == (1 - fair) - NO ask
        if yes_edge >= self.cfg.min_edge:
            score = min(1.0, yes_edge / self.cfg.edge_scale)
        elif no_edge >= self.cfg.min_edge:
            score = -min(1.0, no_edge / self.cfg.edge_scale)
        else:
            return SignalOutput(self.name, 0.0, 0.0, reason=f"fair={fair:.3f} inside spread")
        return SignalOutput(self.name, score, 1.0, reason=f"fair={fair:.3f}")
