"""Core domain types shared by every layer (sources, signals, engine, sim)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    YES = "YES"
    NO = "NO"

    @property
    def sign(self) -> int:
        return 1 if self is Side.YES else -1

    @property
    def opposite(self) -> Side:
        return Side.NO if self is Side.YES else Side.YES


@dataclass(frozen=True)
class Market:
    """A short-duration binary market. YES pays $1 if the underlying closes >= strike."""

    market_id: str
    underlying: str
    open_ts: float
    close_ts: float
    strike: float

    def time_left(self, now: float) -> float:
        return max(0.0, self.close_ts - now)

    def elapsed_frac(self, now: float) -> float:
        span = self.close_ts - self.open_ts
        if span <= 0:
            return 1.0
        return min(1.0, max(0.0, (now - self.open_ts) / span))


@dataclass(frozen=True)
class Level:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    """YES-token book. The NO side is implied: buying NO at q == selling YES at 1 - q."""

    market_id: str
    ts: float
    bids: tuple[Level, ...]  # best first (descending)
    asks: tuple[Level, ...]  # best first (ascending)

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 1.0

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    def entry_price(self, side: Side) -> float:
        """Best price to *buy* `side`, in that side's terms."""
        return self.best_ask if side is Side.YES else round(1.0 - self.best_bid, 4)

    def entry_levels(self, side: Side) -> list[Level]:
        """Levels you would lift to buy `side`, cheapest first."""
        if side is Side.YES:
            return list(self.asks)
        return [Level(round(1.0 - b.price, 4), b.size) for b in self.bids]


# --------------------------------------------------------------------------- events
# Everything the bot reacts to is one of these. Live feeds, the simulator and the
# JSONL replayer all produce the same stream, so the engine never knows which it's on.


@dataclass(frozen=True)
class UnderlyingTick:
    ts: float
    symbol: str
    price: float


@dataclass(frozen=True)
class MarketOpened:
    ts: float
    market: Market


@dataclass(frozen=True)
class BookUpdate:
    ts: float
    book: OrderBook


@dataclass(frozen=True)
class PublicTrade:
    """A trade printed on the public tape, attributed to an account."""

    ts: float
    market_id: str
    account: str
    side: Side
    price: float
    size: float


@dataclass(frozen=True)
class OperatorSignal:
    """A human-entered directional bias that expires after `ttl_s` seconds."""

    ts: float
    value: float
    ttl_s: float
    note: str = ""


@dataclass(frozen=True)
class MarketResolved:
    ts: float
    market_id: str
    outcome: Side


@dataclass(frozen=True)
class Fill:
    ts: float
    order_id: int
    market_id: str
    side: Side
    price: float
    size: float
    fee: float = 0.0


@dataclass(frozen=True)
class OrderClosed:
    """An order is finished (filled, partly filled, expired or cancelled). Nothing more will fill."""

    ts: float
    order_id: int
    market_id: str
    filled: float


Event = UnderlyingTick | MarketOpened | BookUpdate | PublicTrade | OperatorSignal | MarketResolved | Fill | OrderClosed


# --------------------------------------------------------------------------- engine I/O


@dataclass(frozen=True)
class OrderRequest:
    """Immediate-or-cancel buy of `side` up to `limit_price`."""

    order_id: int
    market_id: str
    side: Side
    limit_price: float
    size: float
    created_ts: float


@dataclass(frozen=True)
class SignalOutput:
    name: str
    score: float = 0.0  # [-1, 1]; positive favours YES
    confidence: float = 0.0  # [0, 1]
    veto: bool = False
    reason: str = ""

    @staticmethod
    def neutral(name: str, reason: str = "") -> SignalOutput:
        return SignalOutput(name=name, reason=reason)
