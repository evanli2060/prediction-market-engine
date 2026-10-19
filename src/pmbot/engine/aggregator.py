"""Combine signal opinions into one trade decision.

1. Drop signals the decay monitor has switched off in the current regime.
2. Any veto kills the trade.
3. Weighted sum of score * confidence, normalised by the total weight of
   *enabled* signals (so a lone signal can't look stronger than it is).
4. If signals point in opposite directions, shrink the result in proportion to
   how evenly they disagree.
5. Scale by the regime multiplier, convert to a probability estimate around
   the book mid, and only act if that clears the spread by `min_edge`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import AggregatorConfig
from ..models import OrderBook, Side, SignalOutput
from ..pricing import clamp
from .monitor import DecayMonitor
from .regime import RegimeState


@dataclass(frozen=True)
class Decision:
    side: Side
    prob: float  # our P(YES)
    entry_price: float  # best price to buy `side`
    edge: float
    score: float
    regime: str
    contributions: dict[str, float] = field(default_factory=dict)

    @property
    def side_prob(self) -> float:
        return self.prob if self.side is Side.YES else 1.0 - self.prob


class Aggregator:
    def __init__(self, cfg: AggregatorConfig, monitor: DecayMonitor | None = None) -> None:
        self.cfg = cfg
        self.monitor = monitor

    def combine(
        self,
        outputs: list[SignalOutput],
        regime: RegimeState,
        book: OrderBook,
        now: float,
    ) -> Decision | None:
        active = [
            o
            for o in outputs
            if self.cfg.weights.get(o.name, 0.0) > 0
            and (self.monitor is None or self.monitor.is_enabled(o.name, regime.label, now))
        ]
        if any(o.veto for o in active):
            return None
        total_w = sum(self.cfg.weights[o.name] for o in active)
        if total_w <= 0:
            return None

        contrib = {
            o.name: self.cfg.weights[o.name] * o.confidence * o.score for o in active if o.score and o.confidence
        }
        if not contrib:
            return None
        raw = sum(contrib.values()) / total_w

        pos = sum(c for c in contrib.values() if c > 0)
        neg = -sum(c for c in contrib.values() if c < 0)
        if pos > 0 and neg > 0:
            raw *= 1.0 - self.cfg.disagreement_penalty * (min(pos, neg) / max(pos, neg))

        score = raw * regime.multiplier
        if score == 0:
            return None
        prob = clamp(book.mid + score * self.cfg.max_prob_shift, 0.01, 0.99)

        yes_edge = prob - book.entry_price(Side.YES)
        no_edge = (1.0 - prob) - book.entry_price(Side.NO)
        if yes_edge >= self.cfg.min_edge and yes_edge >= no_edge:
            side, edge = Side.YES, yes_edge
        elif no_edge >= self.cfg.min_edge:
            side, edge = Side.NO, no_edge
        else:
            return None
        return Decision(
            side=side,
            prob=prob,
            entry_price=book.entry_price(side),
            edge=edge,
            score=score,
            regime=regime.label,
            contributions=contrib,
        )
