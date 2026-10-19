"""In-memory view of the world, updated incrementally on every event.

Updates are O(1) amortized per event (running sums, bounded deques), and reads
touch at most the lookback window, never the full history. That matters when the
whole decision has to fit inside a latency budget.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import log, pi, sqrt

from .models import Market, OrderBook


class UnderlyingState:
    """Rolling price history plus running sums for realized volatility.

    Two estimators over the same window, both per second of time:

    * ``rv``: realized variance, sum of squared returns.
    * ``bipower``: (pi/2) * sum |r_t| * |r_t-1|. A single jump inflates one
      return, and the product of neighbours mostly ignores it, so one large move
      doesn't distort the estimate for the rest of the window.
    """

    def __init__(self, symbol: str, window_s: float, price_window_s: float = 300.0, estimator: str = "bipower") -> None:
        if estimator not in ("rv", "bipower"):
            raise ValueError(f"unknown vol estimator {estimator!r}")
        self.symbol = symbol
        self.window_s = window_s
        self.price_window_s = price_window_s
        self.estimator = estimator
        self.prices: deque[tuple[float, float]] = deque()
        self._rets: deque[tuple[float, float, float, float]] = deque()  # (ts, r^2, |r||r_prev|, dt)
        self._sum_r2 = 0.0
        self._sum_bp = 0.0
        self._sum_dt = 0.0
        self._prev_abs_r: float | None = None

    @property
    def last_price(self) -> float | None:
        return self.prices[-1][1] if self.prices else None

    @property
    def last_ts(self) -> float | None:
        return self.prices[-1][0] if self.prices else None

    @property
    def n_returns(self) -> int:
        return len(self._rets)

    def on_tick(self, ts: float, price: float) -> None:
        if self.prices:
            prev_ts, prev_px = self.prices[-1]
            dt = ts - prev_ts
            if dt > 0 and prev_px > 0 and price > 0:
                r = log(price / prev_px)
                bp = abs(r) * self._prev_abs_r if self._prev_abs_r is not None else r * r
                self._prev_abs_r = abs(r)
                self._rets.append((ts, r * r, bp, dt))
                self._sum_r2 += r * r
                self._sum_bp += bp
                self._sum_dt += dt
        self.prices.append((ts, price))

        cutoff = ts - self.window_s
        while self._rets and self._rets[0][0] < cutoff:
            _, r2, bp, dt = self._rets.popleft()
            self._sum_r2 -= r2
            self._sum_bp -= bp
            self._sum_dt -= dt
        # keep one point older than the lookback so price_ago() can reach the edge
        price_cutoff = ts - self.price_window_s
        while len(self.prices) > 1 and self.prices[1][0] < price_cutoff:
            self.prices.popleft()

    def price_ago(self, now: float, seconds: float) -> float | None:
        target = now - seconds
        for ts, px in reversed(self.prices):
            if ts <= target:
                return px
        return None

    def vol_per_s(self, min_returns: int = 20) -> float | None:
        if len(self._rets) < min_returns or self._sum_dt <= 0:
            return None
        total = self._sum_bp * (pi / 2) if self.estimator == "bipower" else self._sum_r2
        return sqrt(max(total, 0.0) / self._sum_dt)


@dataclass
class MarketView:
    market: Market
    book: OrderBook | None = None
    last_eval_ts: float | None = None
    last_order_ts: float | None = None


class WorldState:
    def __init__(self, vol_window_s: float, price_window_s: float = 300.0, vol_estimator: str = "bipower") -> None:
        self.vol_window_s = vol_window_s
        self.price_window_s = price_window_s
        self.vol_estimator = vol_estimator
        self.underlyings: dict[str, UnderlyingState] = {}
        self.markets: dict[str, MarketView] = {}
        self._by_key: dict[tuple[str, float], str] = {}

    def on_tick(self, symbol: str, ts: float, price: float) -> None:
        u = self.underlyings.get(symbol)
        if u is None:
            u = self.underlyings[symbol] = UnderlyingState(
                symbol, self.vol_window_s, self.price_window_s, self.vol_estimator
            )
        u.on_tick(ts, price)

    def add_market(self, market: Market) -> None:
        self.markets[market.market_id] = MarketView(market)
        self._by_key[(market.underlying, market.close_ts)] = market.market_id

    def remove_market(self, market_id: str) -> MarketView | None:
        view = self.markets.pop(market_id, None)
        if view:
            self._by_key.pop((view.market.underlying, view.market.close_ts), None)
        return view

    def on_book(self, book: OrderBook) -> None:
        view = self.markets.get(book.market_id)
        if view:
            view.book = book

    def sibling(self, underlying: str, close_ts: float) -> MarketView | None:
        """The market on another underlying that resolves at the same moment."""
        mid = self._by_key.get((underlying, close_ts))
        return self.markets.get(mid) if mid else None
