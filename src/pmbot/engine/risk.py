"""Position sizing and pre-trade risk checks."""

from __future__ import annotations

from collections.abc import Iterable
from math import floor

from ..config import RiskConfig, SizingConfig
from ..models import Market, OrderRequest, Side
from .aggregator import Decision
from .portfolio import Portfolio


def kelly_shares(decision: Decision, bankroll: float, cfg: SizingConfig) -> float:
    """Fractional Kelly for buying a binary contract at price c with win prob p."""
    c, p = decision.entry_price, decision.side_prob
    if not 0 < c < 1 or p <= c:
        return 0.0
    f = (p - c) / (1.0 - c)
    stake = min(bankroll * cfg.kelly_fraction * f, cfg.max_stake)
    shares = floor(stake / c)
    return float(shares) if shares >= cfg.min_shares else 0.0


class RiskManager:
    def __init__(self, cfg: RiskConfig) -> None:
        self.cfg = cfg

    def pre_check(self, market: Market, now: float, last_order_ts: float | None) -> str | None:
        """Cheap, side-independent checks. Run before evaluating any signal."""
        if now - market.open_ts < self.cfg.entry_delay_s:
            return "too_early"
        if market.time_left(now) < self.cfg.entry_cutoff_s:
            return "inside_cutoff"
        if last_order_ts is not None and now - last_order_ts < self.cfg.min_order_interval_s:
            return "cooldown"
        return None

    def price_ok(self, entry_price: float) -> bool:
        return self.cfg.min_entry_price <= entry_price <= self.cfg.max_entry_price

    def max_shares(
        self,
        market: Market,
        side: Side,
        limit_price: float,
        portfolio: Portfolio,
        now: float,
        inflight: Iterable[OrderRequest] = (),
    ) -> tuple[float, str]:
        """Largest order that keeps every limit intact even if it fills entirely at `limit_price`.

        `inflight` is every order sent but not yet closed. Their worst-case notional
        counts against the limits, and an in-flight order on the other side blocks
        this one: without that, a slow venue lets the bot buy both sides of a market.
        """
        if portfolio.realized_today(now) <= -self.cfg.daily_loss_limit:
            return 0.0, "daily_loss_limit"
        inflight = list(inflight)
        pos = portfolio.positions.get(market.market_id)
        if (pos is not None and pos.holds(side.opposite)) or any(
            o.market_id == market.market_id and o.side is side.opposite for o in inflight
        ):
            return 0.0, "opposite_position"

        reserved_total = sum(o.limit_price * o.size for o in inflight)
        reserved_here = sum(o.limit_price * o.size for o in inflight if o.market_id == market.market_id)
        room = min(
            self.cfg.max_position_per_market - (pos.cost if pos else 0.0) - reserved_here,
            self.cfg.max_total_exposure - portfolio.exposure() - reserved_total,
            portfolio.cash - reserved_total,
        )
        shares = floor(room / limit_price + 1e-9) if limit_price > 0 else 0
        if shares < 1:
            return 0.0, "limit"
        return float(shares), "ok"
