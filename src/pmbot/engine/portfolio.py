"""Positions, cash and per-trade records, settled when markets resolve."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from ..models import Fill, Side


@dataclass
class Position:
    """Holdings in one market. Both sides are tracked so an unexpected opposite
    fill (possible live, in a race) is accounted for instead of crashing."""

    market_id: str
    shares: dict[Side, float] = field(default_factory=lambda: {Side.YES: 0.0, Side.NO: 0.0})
    cost: float = 0.0

    def holds(self, side: Side) -> bool:
        return self.shares[side] > 0


@dataclass
class TradeRecord:
    order_id: int
    market_id: str
    underlying: str
    side: Side
    price: float
    size: float
    fee: float
    ts: float
    regime: str
    contributions: dict[str, float] = field(default_factory=dict)
    pnl: float | None = None
    # horizon (s) -> book mid on our side minus our price. Positive: the book moved our way.
    markouts: dict[int, float] = field(default_factory=dict)

    @property
    def cost(self) -> float:
        return self.price * self.size + self.fee


class Portfolio:
    def __init__(self, starting_cash: float, tz: str = "America/New_York") -> None:
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.tz = ZoneInfo(tz)
        self.positions: dict[str, Position] = {}
        self.trades: list[TradeRecord] = []
        self._open: dict[str, list[TradeRecord]] = defaultdict(list)
        self.realized_by_day: dict[str, float] = defaultdict(float)

    def day_key(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self.tz).strftime("%Y-%m-%d")

    def exposure(self) -> float:
        return sum(p.cost for p in self.positions.values())

    def realized_today(self, now: float) -> float:
        return self.realized_by_day.get(self.day_key(now), 0.0)

    def on_fill(self, fill: Fill, *, underlying: str, regime: str, contributions: dict[str, float]) -> TradeRecord:
        rec = TradeRecord(
            order_id=fill.order_id,
            market_id=fill.market_id,
            underlying=underlying,
            side=fill.side,
            price=fill.price,
            size=fill.size,
            fee=fill.fee,
            ts=fill.ts,
            regime=regime,
            contributions=dict(contributions),
        )
        self.cash -= rec.cost
        pos = self.positions.get(fill.market_id)
        if pos is None:
            pos = self.positions[fill.market_id] = Position(fill.market_id)
        pos.shares[fill.side] += fill.size
        pos.cost += rec.cost
        self.trades.append(rec)
        self._open[fill.market_id].append(rec)
        return rec

    def settle(self, market_id: str, outcome: Side, ts: float) -> list[TradeRecord]:
        self.positions.pop(market_id, None)
        records = self._open.pop(market_id, [])
        day = self.day_key(ts)
        for r in records:
            payout = r.size if r.side is outcome else 0.0
            pnl = payout - r.cost
            r.pnl = pnl
            self.cash += payout
            self.realized_by_day[day] += pnl
        return records
