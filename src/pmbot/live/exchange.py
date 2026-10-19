"""Live exchange adapter (stub).

Implements the same `ExchangeClient` protocol as `PaperExchange`. Fills do not
come back from `on_market_event`; they arrive through the user-channel
websocket as `Fill` events in the main stream.
"""

from __future__ import annotations

from ..models import Event, Fill, OrderRequest


class LiveExchange:
    """Production notes (approach only):

    * sign orders locally; keep the HTTP session warm so submission is one round trip
    * send IOC/FOK limit orders at `order.limit_price`; never market orders
    * record submit time and venue ack time per order to measure real latency
    * on any auth or rate-limit error, stop submitting and alert rather than retry blindly
    """

    def __init__(self, *_, **__) -> None:
        raise NotImplementedError("live order routing is not included in this template")

    async def submit(self, order: OrderRequest) -> None:  # pragma: no cover
        raise NotImplementedError

    def on_market_event(self, ev: Event) -> list[Fill]:  # pragma: no cover
        return []
