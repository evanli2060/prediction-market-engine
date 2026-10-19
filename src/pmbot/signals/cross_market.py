"""Leader -> follower relationship between markets that resolve at the same time.

Correlated underlyings tend to finish on the same side of their strikes. When
the leader's market is already strongly one-sided and the follower's market
hasn't caught up, lean the follower toward the leader.
"""

from __future__ import annotations

from ..config import CrossMarketConfig
from ..models import SignalOutput
from .base import SignalContext


class CrossMarketSignal:
    name = "cross_market"

    def __init__(self, cfg: CrossMarketConfig) -> None:
        self.cfg = cfg

    def evaluate(self, ctx: SignalContext) -> SignalOutput:
        m = ctx.view.market
        leader_sym = self.cfg.leader_of.get(m.underlying)
        if not leader_sym:
            return SignalOutput.neutral(self.name, "not a follower")
        leader = ctx.world.sibling(leader_sym, m.close_ts)
        if leader is None or leader.book is None or ctx.view.book is None:
            return SignalOutput.neutral(self.name, "no leader book")

        lp, fp = leader.book.mid, ctx.view.book.mid
        hi = self.cfg.leader_threshold
        if lp >= hi and lp - fp >= self.cfg.min_gap:
            score = min(1.0, (lp - fp) / self.cfg.gap_scale)
        elif lp <= 1 - hi and fp - lp >= self.cfg.min_gap:
            score = -min(1.0, (fp - lp) / self.cfg.gap_scale)
        else:
            return SignalOutput.neutral(self.name, f"leader={lp:.2f} follower={fp:.2f}")
        return SignalOutput(self.name, score, self.cfg.agreement_rate, reason=f"leader={lp:.2f} follower={fp:.2f}")
