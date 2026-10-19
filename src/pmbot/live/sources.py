"""Live event source: many websocket producers, one ordered stream.

Each producer is a coroutine that connects to one feed, normalises its
messages into `pmbot.models` events and puts them on a shared queue. The bot
consumes the queue exactly like it consumes the simulator.

The merging and operator console below are complete. The feed producers are
intentionally left as documented stubs: the production versions are not part
of this repository.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import IO, Any

from ..models import Event, OperatorSignal

Producer = Callable[[asyncio.Queue], Coroutine[Any, Any, None]]


class LiveEventSource:
    """Merge producers into one stream.

    * A producer that raises stops the stream with that exception: trading on a
      feed that silently died is worse than stopping.
    * A producer that returns normally (e.g. stdin closed) is simply dropped.
    * The stream ends once every producer has finished and the queue is drained.
    """

    def __init__(self, producers: list[Producer], maxsize: int = 50_000) -> None:
        self.producers = producers
        self.maxsize = maxsize

    async def events(self) -> AsyncIterator[Event]:
        queue: asyncio.Queue = asyncio.Queue(self.maxsize)
        running = {asyncio.create_task(p(queue)) for p in self.producers}
        getter: asyncio.Task | None = None
        try:
            while running or not queue.empty():
                if getter is None:
                    getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({getter, *running}, return_when=asyncio.FIRST_COMPLETED)
                for task in done - {getter}:
                    running.discard(task)
                    exc = None if task.cancelled() else task.exception()
                    if exc is not None:
                        raise exc
                if getter in done:
                    event, getter = getter.result(), None
                    yield event
        finally:
            if getter is not None:
                getter.cancel()
            for task in running:
                task.cancel()


# ----------------------------------------------------------------------------- stubs


async def underlying_price_producer(queue: asyncio.Queue, symbols: list[str]) -> None:
    """Spot-exchange trade or best-bid/offer stream -> UnderlyingTick.

    Production notes (approach only):
      * one websocket, all symbols multiplexed; reconnect with jittered backoff
      * timestamp with the exchange's event time, not receipt time, and track
        the gap between them as a latency metric
      * drop out-of-order messages rather than reorder them
    """
    raise NotImplementedError("connect your price feed here")


async def market_book_producer(queue: asyncio.Queue, market_ids: list[str]) -> None:
    """Prediction-market CLOB websocket -> BookUpdate (and PublicTrade from the trade channel).

    Production notes (approach only):
      * subscribe to the market channel for each active token; apply snapshots,
        then deltas; resnapshot on sequence gaps
      * emit a BookUpdate on every change, carrying the venue timestamp
      * new markets appear continuously, so subscriptions are driven by discovery
    """
    raise NotImplementedError("connect the venue's market websocket here")


async def market_discovery_producer(queue: asyncio.Queue, poll_s: float = 15.0) -> None:
    """Poll the venue's market listing for new short-duration markets -> MarketOpened.

    Resolution can come from the same poll (-> MarketResolved).
    """
    raise NotImplementedError("poll the venue's market metadata API here")


# ----------------------------------------------------------------------------- operator


def parse_operator_line(line: str, now: float, default_ttl_s: float = 120.0) -> OperatorSignal | None:
    """'+', '-', '0', optionally followed by a TTL in seconds and a note.

    '+ 90 hawkish surprise'  -> +1 for 90s
    '-'                      -> -1 for the default TTL
    '0'                      -> clear
    """
    parts = line.strip().split(maxsplit=2)
    if not parts or parts[0] not in {"+", "-", "0"}:
        return None
    value = {"+": 1.0, "-": -1.0, "0": 0.0}[parts[0]]
    ttl = default_ttl_s
    note = ""
    if len(parts) > 1:
        try:
            ttl = float(parts[1])
            note = parts[2] if len(parts) > 2 else ""
        except ValueError:
            note = " ".join(parts[1:])
    return OperatorSignal(now, value, 0.0 if value == 0 else ttl, note)


async def operator_console_producer(queue: asyncio.Queue, stream: IO[str] | None = None) -> None:
    """Read operator overrides from a text stream (stdin by default) without blocking the loop.

    Reading happens on a daemon thread, so a pending readline never stops the
    process from exiting (a default-executor thread would hang shutdown).
    """
    src: IO[str] = stream if stream is not None else sys.stdin
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[str | None] = asyncio.Queue()

    def pump() -> None:
        try:
            for line in src:
                loop.call_soon_threadsafe(lines.put_nowait, line)
            loop.call_soon_threadsafe(lines.put_nowait, None)
        except RuntimeError:  # loop already closed during shutdown
            pass

    threading.Thread(target=pump, name="operator-console", daemon=True).start()
    while (line := await lines.get()) is not None:
        sig = parse_operator_line(line, time.time())
        if sig is not None:
            await queue.put(sig)
