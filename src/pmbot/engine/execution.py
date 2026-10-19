"""Turning a sized decision into an order, and the exchange interface it goes to."""

from __future__ import annotations

from itertools import count
from math import floor
from typing import Protocol

from ..config import ExecutionConfig
from ..models import Event, Fill, OrderBook, OrderClosed, OrderRequest
from .aggregator import Decision


class ExchangeClient(Protocol):
    async def submit(self, order: OrderRequest) -> None: ...

    def on_market_event(self, ev: Event) -> list[Fill | OrderClosed]:
        """Paper exchanges fill here. Live exchanges return [] and deliver the same
        `Fill` / `OrderClosed` events through the event stream instead.

        Every submitted order must eventually produce exactly one `OrderClosed`;
        risk treats an order as in flight until it does.
        """
        ...


class Executor:
    """Slippage-aware IOC limit orders.

    The limit is the best price plus a slippage allowance, but never so high that
    the fill would leave less than `min_edge_at_limit` of edge. Stale books are
    refused outright: acting on an old quote is how latency turns into losses.
    """

    def __init__(self, cfg: ExecutionConfig, exchange: ExchangeClient) -> None:
        self.cfg = cfg
        self.exchange = exchange
        self._ids = count(1)

    def limit_price(self, decision: Decision, book: OrderBook, now: float) -> float | None:
        """Worst price we'll pay, or None if the book is stale or there's no room for slippage."""
        if now - book.ts > self.cfg.max_book_age_s:
            return None
        limit = min(
            0.99,
            decision.entry_price + self.cfg.max_slippage,
            decision.side_prob - self.cfg.min_edge_at_limit,
        )
        limit = floor(limit * 100 + 1e-9) / 100  # round toward less aggressive
        return limit if limit >= decision.entry_price else None

    def build(self, decision: Decision, limit: float, shares: float, market_id: str, now: float) -> OrderRequest:
        return OrderRequest(
            order_id=next(self._ids),
            market_id=market_id,
            side=decision.side,
            limit_price=limit,
            size=shares,
            created_ts=now,
        )

    async def send(self, order: OrderRequest) -> None:
        await self.exchange.submit(order)
