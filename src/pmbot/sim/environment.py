"""Deterministic market simulator.

Generates the same event stream a live connection would, from a seeded model
that has the structural features the strategy depends on:

* correlated underlyings (BTC leads, ETH follows) with a slow-moving trend term,
  so short-horizon momentum carries real information;
* order books quoted off a *stale* underlying price. The lag shrinks linearly
  over the run to mimic competing bots arriving, which is what makes edge decay
  observable in a multi-day run;
* outside core hours, books set by participants who are effectively ahead of the
  public price, plus more frequent late-window reversals: apparent mispricings
  there are adverse selection, so time-of-day gating has something to find;
* spreads that widen into resolution;
* synthetic accounts on the public tape: a few informed, one that fades an
  informed account, and a crowd of noise;
* scheduled macro events that add drift, with an operator signal shortly after.

None of these parameters are calibrated to real data.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil, exp, floor, log, sqrt
from zoneinfo import ZoneInfo

from ..models import (
    BookUpdate,
    Event,
    Level,
    Market,
    MarketOpened,
    MarketResolved,
    OperatorSignal,
    OrderBook,
    PublicTrade,
    Side,
    UnderlyingTick,
)
from ..pricing import binary_fair_prob, clamp


@dataclass
class InformedAccount:
    name: str
    accuracy: float
    participation: float
    hours: str = "all"  # "all" | "core" | "off"


@dataclass
class FadingAccount:
    """Takes the opposite side shortly after a followed account trades."""

    name: str
    follows: str
    participation: float
    delay_s: tuple[int, int] = (3, 25)


@dataclass
class MacroEvent:
    offset_s: int  # seconds after sim start
    direction: int  # +1 / -1
    duration_s: int = 600
    operator_delay_s: int = 15
    operator_ttl_s: float = 180.0


@dataclass
class SimConfig:
    start: str = "2026-01-05T00:00:00"  # a Monday, local time
    tz: str = "America/New_York"
    days: float = 2.0
    market_len_s: int = 900

    start_prices: dict[str, float] = field(default_factory=lambda: {"BTC": 95_000.0, "ETH": 3_300.0})
    vol_per_s: dict[str, float] = field(default_factory=lambda: {"BTC": 1.1e-4, "ETH": 1.4e-4})
    correlation: float = 0.85
    trend_persistence: float = 0.995
    trend_strength: float = 0.02
    macro_strength: float = 0.08

    book_lag_start_s: float = 4.0
    book_lag_end_s: float = 1.0
    book_lag_mult: dict[str, float] = field(default_factory=lambda: {"BTC": 1.0, "ETH": 1.6})
    book_noise: float = 0.01
    spread: float = 0.02
    late_spread_widen: float = 0.06  # makers widen as resolution approaches...
    late_spread_window_s: int = 240  # ...over this many final seconds
    depth_levels: int = 3
    level_size: float = 150.0

    core_hours: tuple[str, str] = ("09:30", "16:00")
    # Off-hours the book is set by participants who are effectively *ahead* of the
    # public price (modelled crudely as quoting off a price this many seconds in the
    # future). Apparent mispricings there are adverse selection, not edge.
    offhours_book_lead_s: float = 30.0
    late_window_s: int = 60
    jump_prob_core: float = 0.0003
    jump_prob_off: float = 0.002
    jump_size_window_sd: float = 1.5

    informed: list[InformedAccount] = field(
        default_factory=lambda: [
            InformedAccount("sim_acct_03", accuracy=0.64, participation=0.4),
            InformedAccount("sim_acct_07", accuracy=0.62, participation=0.4, hours="core"),
        ]
    )
    faders: list[FadingAccount] = field(
        default_factory=lambda: [FadingAccount("sim_acct_11", follows="sim_acct_03", participation=0.6)]
    )
    n_noise_accounts: int = 8
    noise_participation: float = 0.25

    macro_events: list[MacroEvent] = field(default_factory=lambda: [MacroEvent(14 * 3600, -1)])


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


class SimulatedEnvironment:
    def __init__(self, cfg: SimConfig | None = None, seed: int = 7) -> None:
        self.cfg = cfg or SimConfig()
        self.seed = seed
        tz = ZoneInfo(self.cfg.tz)
        self.tz = tz
        self.t0 = datetime.fromisoformat(self.cfg.start).replace(tzinfo=tz).timestamp()
        self.n_steps = int(self.cfg.days * 86400) + 1
        self.symbols = list(self.cfg.start_prices)
        self._core = self._core_flags()
        self.paths = self._generate_paths(random.Random(seed))
        self.trades_by_step, self.operator_by_step = self._schedule_flow(random.Random(seed + 1))
        self._book_rng = random.Random(seed + 2)
        # Makers quote with the true long-horizon vol (diffusion + slow trend), so
        # tails aren't free money. A per-second realized-vol estimate misses the trend part.
        rho = self.cfg.trend_persistence
        mult = sqrt(1 + self.cfg.trend_strength**2 * (1 + rho) / (1 - rho))
        self._maker_vol = {s: v * mult for s, v in self.cfg.vol_per_s.items()}

    # ---------------------------------------------------------------- generation

    def _core_flags(self) -> list[bool]:
        lo, hi = map(_hhmm, self.cfg.core_hours)
        flags = []
        for minute in range(self.n_steps // 60 + 2):
            dt = datetime.fromtimestamp(self.t0 + minute * 60, self.tz)
            flags.append(dt.weekday() < 5 and lo <= dt.hour * 60 + dt.minute < hi)
        return flags

    def is_core(self, step: int) -> bool:
        return self._core[step // 60]

    def _generate_paths(self, rng: random.Random) -> dict[str, list[float]]:
        cfg, n, L = self.cfg, self.n_steps, self.cfg.market_len_s
        macro = [0] * n
        for ev in cfg.macro_events:
            for i in range(ev.offset_s, min(n, ev.offset_s + ev.duration_s)):
                macro[i] = ev.direction

        logp = {s: log(cfg.start_prices[s]) for s in self.symbols}
        paths = {s: [0.0] * n for s in self.symbols}
        strike = dict(logp)
        trend = 0.0
        eta = sqrt(1 - cfg.trend_persistence**2)
        c = cfg.correlation
        for i in range(n):
            if i % L == 0:
                strike = dict(logp)
            for s in self.symbols:
                paths[s][i] = round(exp(logp[s]), 2)

            late = i % L >= L - cfg.late_window_s
            jump_p = cfg.jump_prob_core if self.is_core(i) else cfg.jump_prob_off
            trend = cfg.trend_persistence * trend + eta * rng.gauss(0, 1)
            z_common = rng.gauss(0, 1)
            for k, s in enumerate(self.symbols):
                vol = cfg.vol_per_s[s]
                z = z_common if k == 0 else c * z_common + sqrt(1 - c * c) * rng.gauss(0, 1)
                logp[s] += (trend * cfg.trend_strength + macro[i] * cfg.macro_strength) * vol + vol * z
                if late and rng.random() < jump_p:
                    leading = 1 if logp[s] >= strike[s] else -1
                    logp[s] -= leading * cfg.jump_size_window_sd * vol * sqrt(L)
        return paths

    def windows(self) -> list[tuple[int, int, int]]:
        """(k, open_step, close_step) for every full market window in the run."""
        L = self.cfg.market_len_s
        out, k = [], 0
        while (k + 1) * L < self.n_steps:
            out.append((k, k * L, (k + 1) * L))
            k += 1
        return out

    def market_id(self, sym: str, k: int) -> str:
        return f"{sym}-15m-{k:04d}"

    def outcome(self, sym: str, open_step: int, close_step: int) -> Side:
        p = self.paths[sym]
        return Side.YES if p[close_step] >= p[open_step] else Side.NO

    def _schedule_flow(self, rng: random.Random):
        cfg = self.cfg
        trades: dict[int, list[tuple[str, str, Side, float]]] = {}
        noise = [f"sim_acct_{50 + j:02d}" for j in range(cfg.n_noise_accounts)]

        def add(step: int, *item) -> None:
            trades.setdefault(step, []).append(item)

        for k, o, c in self.windows():
            core = self.is_core(o)
            for sym in self.symbols:
                mid = self.market_id(sym, k)
                outcome = self.outcome(sym, o, c)
                placed: dict[str, tuple[int, Side]] = {}
                for acct in cfg.informed:
                    if (acct.hours == "core" and not core) or (acct.hours == "off" and core):
                        continue
                    if rng.random() >= acct.participation:
                        continue
                    side = outcome if rng.random() < acct.accuracy else outcome.opposite
                    step = o + rng.randint(60, 420)
                    add(step, mid, acct.name, side, float(rng.randint(50, 250)))
                    placed[acct.name] = (step, side)
                for f in cfg.faders:
                    if f.follows in placed and rng.random() < f.participation:
                        step, side = placed[f.follows]
                        step = min(c - 1, step + rng.randint(*f.delay_s))
                        add(step, mid, f.name, side.opposite, float(rng.randint(50, 250)))
                for name in noise:
                    if rng.random() < cfg.noise_participation:
                        side = Side.YES if rng.random() < 0.5 else Side.NO
                        add(o + rng.randint(30, cfg.market_len_s - 30), mid, name, side, float(rng.randint(20, 200)))

        ops: dict[int, list[OperatorSignal]] = {}
        for ev in cfg.macro_events:
            step = ev.offset_s + ev.operator_delay_s
            if step < self.n_steps:
                ops.setdefault(step, []).append(
                    OperatorSignal(self.t0 + step, float(ev.direction), ev.operator_ttl_s, "scheduled macro event")
                )
        return trades, ops

    # ---------------------------------------------------------------- quoting

    def _book_lag(self, sym: str, step: int) -> float:
        frac = step / max(1, self.n_steps - 1)
        base = self.cfg.book_lag_start_s + (self.cfg.book_lag_end_s - self.cfg.book_lag_start_s) * frac
        return base * self.cfg.book_lag_mult.get(sym, 1.0)

    def _quote(self, market: Market, sym: str, open_step: int, close_step: int, step: int) -> OrderBook:
        cfg = self.cfg
        if self.is_core(step):
            ref_step = max(open_step, step - round(self._book_lag(sym, step)))
        else:
            ref_step = min(close_step, step + round(cfg.offhours_book_lead_s))
        fair = binary_fair_prob(self.paths[sym][ref_step], market.strike, self._maker_vol[sym], close_step - ref_step)
        mid = clamp(fair + self._book_rng.gauss(0, cfg.book_noise), 0.02, 0.98)
        t_left = close_step - step
        spread = cfg.spread
        if t_left < cfg.late_spread_window_s:
            spread += cfg.late_spread_widen * (1 - t_left / cfg.late_spread_window_s)
        bid0 = max(0.01, floor((mid - spread / 2) * 100 + 1e-9) / 100)
        ask0 = min(0.99, ceil((mid + spread / 2) * 100 - 1e-9) / 100)
        if ask0 <= bid0:
            ask0 = round(bid0 + 0.01, 2)
        bids = tuple(
            Level(round(bid0 - 0.01 * j, 2), cfg.level_size * (1 + j))
            for j in range(cfg.depth_levels)
            if bid0 - 0.01 * j >= 0.01 - 1e-9
        )
        asks = tuple(
            Level(round(ask0 + 0.01 * j, 2), cfg.level_size * (1 + j))
            for j in range(cfg.depth_levels)
            if ask0 + 0.01 * j <= 0.99 + 1e-9
        )
        return OrderBook(market.market_id, self.t0 + step, bids, asks)

    # ---------------------------------------------------------------- stream

    async def events(self) -> AsyncIterator[Event]:
        L = self.cfg.market_len_s
        windows = {o: (k, o, c) for k, o, c in self.windows()}
        live: dict[str, tuple[Market, str, int, int]] = {}
        mids: dict[str, float] = {}

        for i in range(self.n_steps):
            ts = self.t0 + i
            for s in self.symbols:
                yield UnderlyingTick(ts, s, self.paths[s][i])

            if i % L == 0:
                for mid_id, (_m, s, o, c) in list(live.items()):
                    if c == i:
                        del live[mid_id]
                        mids.pop(mid_id, None)
                        yield MarketResolved(ts, mid_id, self.outcome(s, o, c))
                if i in windows:
                    k, o, c = windows[i]
                    for s in self.symbols:
                        m = Market(self.market_id(s, k), s, ts, self.t0 + c, self.paths[s][o])
                        live[m.market_id] = (m, s, o, c)
                        yield MarketOpened(ts, m)

            for mid_id, (m, s, o, c) in live.items():
                book = self._quote(m, s, o, c, i)
                mids[mid_id] = book.mid
                yield BookUpdate(ts, book)

            for mid_id, acct, side, size in self.trades_by_step.get(i, ()):
                if mid_id in mids:
                    px = mids[mid_id] if side is Side.YES else 1 - mids[mid_id]
                    yield PublicTrade(ts, mid_id, acct, side, round(px, 2), size)

            for op in self.operator_by_step.get(i, ()):
                yield op

            if i % 2000 == 0:
                await asyncio.sleep(0)  # let other tasks breathe in a shared loop
