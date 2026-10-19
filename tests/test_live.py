import asyncio
import io

import pytest

from pmbot.live import LiveEventSource, operator_console_producer
from pmbot.models import OperatorSignal, UnderlyingTick


def ticks(symbol, n, delay=0.0):
    async def producer(queue):
        for i in range(n):
            await queue.put(UnderlyingTick(float(i), symbol, 100.0 + i))
            await asyncio.sleep(delay)

    return producer


async def collect(source, timeout=2.0):
    async def run():
        return [ev async for ev in source.events()]

    return await asyncio.wait_for(run(), timeout)


def test_merges_producers_and_ends_when_all_finish():
    events = asyncio.run(collect(LiveEventSource([ticks("BTC", 5), ticks("ETH", 3, delay=0.01)])))
    assert sorted(e.symbol for e in events) == ["BTC"] * 5 + ["ETH"] * 3


def test_a_finished_producer_does_not_spin_or_stall_the_others():
    async def quiet(queue):
        return  # e.g. stdin closed immediately

    events = asyncio.run(collect(LiveEventSource([quiet, ticks("BTC", 20, delay=0.005)])))
    assert len(events) == 20


def test_a_crashed_producer_stops_the_stream():
    async def broken(queue):
        await asyncio.sleep(0.01)
        raise ConnectionError("feed lost")

    with pytest.raises(ConnectionError):
        asyncio.run(collect(LiveEventSource([broken, ticks("BTC", 1000, delay=0.005)])))


def test_operator_console_reads_lines_and_skips_junk():
    stream = io.StringIO("+ 90 hawkish\nhello\n-\n")
    events = asyncio.run(collect(LiveEventSource([lambda q: operator_console_producer(q, stream)])))
    assert [(e.value, e.ttl_s) for e in events if isinstance(e, OperatorSignal)] == [(1.0, 90.0), (-1.0, 120.0)]
