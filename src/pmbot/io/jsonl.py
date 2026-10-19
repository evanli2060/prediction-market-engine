"""Record any event stream to JSONL and replay it later.

Recording live market data and replaying it through the same engine (with the
paper exchange) is how strategy changes get tested before they touch money.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path

from ..models import (
    BookUpdate,
    Event,
    Fill,
    Level,
    Market,
    MarketOpened,
    MarketResolved,
    OperatorSignal,
    OrderBook,
    OrderClosed,
    PublicTrade,
    Side,
    UnderlyingTick,
)

_TYPES = {
    cls.__name__: cls
    for cls in (
        UnderlyingTick,
        MarketOpened,
        BookUpdate,
        PublicTrade,
        OperatorSignal,
        MarketResolved,
        Fill,
        OrderClosed,
    )
}


def event_to_dict(ev: Event) -> dict:
    d = asdict(ev)
    d["type"] = type(ev).__name__
    return d


def event_from_dict(d: dict) -> Event:
    d = dict(d)
    cls = _TYPES[d.pop("type")]
    if cls is MarketOpened:
        d["market"] = Market(**d["market"])
    elif cls is BookUpdate:
        b = d["book"]
        d["book"] = OrderBook(
            b["market_id"],
            b["ts"],
            tuple(Level(**lv) for lv in b["bids"]),
            tuple(Level(**lv) for lv in b["asks"]),
        )
    for key in ("side", "outcome"):
        if key in d:
            d[key] = Side(d[key])
    return cls(**d)


class RecordingSource:
    """Wraps another source and writes every event it yields."""

    def __init__(self, inner, path: str | Path) -> None:
        self.inner = inner
        self.path = Path(path)

    async def events(self) -> AsyncIterator[Event]:
        with self.path.open("w") as fh:
            async for ev in self.inner.events():
                fh.write(json.dumps(event_to_dict(ev), separators=(",", ":")) + "\n")
                yield ev


class ReplaySource:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def events(self) -> AsyncIterator[Event]:
        with self.path.open() as fh:
            for n, line in enumerate(fh):
                if line.strip():
                    yield event_from_dict(json.loads(line))
                if n % 5000 == 0:
                    await asyncio.sleep(0)
