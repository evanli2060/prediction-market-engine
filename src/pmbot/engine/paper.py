"""Paper exchange: fills IOC orders against the book stream with simulated latency.

Shared by the simulator and the JSONL replayer, so a strategy is always
evaluated with the same fill model. An order submitted at t is matched against
the first book snapshot at or after t + latency, which is exactly how a slow
connection loses edge: the quote you decided on is gone by the time you arrive.

Simplification: fills don't consume displayed size from future snapshots.
"""

from __future__ import annotations

from collections import defaultdict

from ..models import BookUpdate, Event, Fill, MarketResolved, OrderClosed, OrderRequest


class PaperExchange:
    def __init__(self, latency_s: float = 0.8, fee_rate: float = 0.0) -> None:
        self.latency_s = latency_s
        self.fee_rate = fee_rate
        self._pending: dict[str, list[OrderRequest]] = defaultdict(list)
        self.submitted = 0
        self.unfilled = 0

    async def submit(self, order: OrderRequest) -> None:
        self._pending[order.market_id].append(order)
        self.submitted += 1

    def on_market_event(self, ev: Event) -> list[Fill | OrderClosed]:
        if isinstance(ev, MarketResolved):
            cancelled = self._pending.pop(ev.market_id, [])
            self.unfilled += len(cancelled)
            return [OrderClosed(ev.ts, o.order_id, o.market_id, 0.0) for o in cancelled]
        if not isinstance(ev, BookUpdate):
            return []
        queue = self._pending.get(ev.book.market_id)
        if not queue:
            return []

        out: list[Fill | OrderClosed] = []
        still_waiting: list[OrderRequest] = []
        for order in queue:
            if ev.ts < order.created_ts + self.latency_s:
                still_waiting.append(order)
                continue
            remaining = order.size
            for level in ev.book.entry_levels(order.side):
                if remaining <= 0 or level.price > order.limit_price:
                    break
                qty = min(remaining, level.size)
                fee = round(qty * level.price * self.fee_rate, 6)
                out.append(Fill(ev.ts, order.order_id, order.market_id, order.side, level.price, qty, fee))
                remaining -= qty
            if remaining == order.size:
                self.unfilled += 1  # IOC: nothing at or inside the limit
            out.append(OrderClosed(ev.ts, order.order_id, order.market_id, order.size - remaining))
        if still_waiting:
            self._pending[ev.book.market_id] = still_waiting
        else:
            self._pending.pop(ev.book.market_id, None)
        return out
