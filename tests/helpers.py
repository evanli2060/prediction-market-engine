from datetime import datetime
from zoneinfo import ZoneInfo

from pmbot.models import Level, Market, OrderBook, SignalOutput

ET = ZoneInfo("America/New_York")


def ts(iso: str) -> float:
    return datetime.fromisoformat(iso).replace(tzinfo=ET).timestamp()


MON_10AM = ts("2026-01-05T10:00:00")


def book(bid: float, ask: float, market_id: str = "M", at: float = MON_10AM, size: float = 100.0) -> OrderBook:
    return OrderBook(
        market_id,
        at,
        (Level(bid, size), Level(round(bid - 0.01, 2), size * 2)),
        (Level(ask, size), Level(round(ask + 0.01, 2), size * 2)),
    )


def market(
    market_id: str = "M", underlying: str = "BTC", open_ts: float = MON_10AM - 300, strike: float = 100.0
) -> Market:
    return Market(market_id, underlying, open_ts, open_ts + 900, strike)


def out(name: str, score: float, confidence: float = 1.0, veto: bool = False) -> SignalOutput:
    return SignalOutput(name, score, confidence, veto)
