"""The event loop.

One coroutine consumes one ordered event stream. Live, simulated and replayed
runs differ only in the `EventSource` and `ExchangeClient` passed in.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import perf_counter
from typing import Protocol

from .config import BotConfig
from .engine import (
    Aggregator,
    DecayMonitor,
    Decision,
    ExchangeClient,
    Executor,
    Portfolio,
    RegimeGate,
    RiskManager,
    TradeRecord,
    kelly_shares,
)
from .models import (
    BookUpdate,
    Event,
    Fill,
    MarketOpened,
    MarketResolved,
    OperatorSignal,
    OrderClosed,
    OrderRequest,
    PublicTrade,
    UnderlyingTick,
)
from .signals import (
    CrossMarketSignal,
    LagMomentumSignal,
    ManualOverrideSignal,
    OrderFlowSignal,
    SignalContext,
)
from .state import WorldState


class EventSource(Protocol):
    def events(self) -> AsyncIterator[Event]: ...


@dataclass
class RunStats:
    events: int = 0
    evaluations: int = 0
    decisions: int = 0
    orders: int = 0
    fills: int = 0
    dropped_over_budget: int = 0
    gated_by_regime: int = 0
    blocked: Counter = field(default_factory=Counter)
    decision_ms: list[float] = field(default_factory=list)


class TradingBot:
    def __init__(self, cfg: BotConfig, source: EventSource, exchange: ExchangeClient) -> None:
        self.cfg = cfg
        self.source = source
        self.exchange = exchange

        self.world = WorldState(
            cfg.lag.vol_window_s,
            price_window_s=max(300.0, cfg.lag.lookback_s * 2),
            vol_estimator=cfg.lag.vol_estimator,
        )
        self.lag = LagMomentumSignal(cfg.lag)
        self.cross = CrossMarketSignal(cfg.cross)
        self.flow = OrderFlowSignal(cfg.flow)
        self.manual = ManualOverrideSignal(cfg.manual)
        self.signals = [self.lag, self.cross, self.flow, self.manual]

        self.regime = RegimeGate(cfg.regime)
        self.monitor = DecayMonitor(cfg.monitor)
        self.aggregator = Aggregator(cfg.aggregator, self.monitor)
        self.risk = RiskManager(cfg.risk)
        self.portfolio = Portfolio(cfg.risk.starting_cash, cfg.regime.tz)
        self.executor = Executor(cfg.execution, exchange)

        self.stats = RunStats()
        self._order_meta: dict[int, Decision] = {}
        self.inflight: dict[int, OrderRequest] = {}  # sent, not yet closed
        self._markouts: dict[str, list[tuple[float, int, TradeRecord]]] = {}  # market -> (due_ts, horizon, rec)
        self.market_open_ts: dict[str, float] = {}  # kept after resolution, for reporting

    async def run(self) -> RunStats:
        async for ev in self.source.events():
            await self.on_event(ev)
        return self.stats

    async def on_event(self, ev: Event) -> None:
        t0 = perf_counter()
        self.stats.events += 1
        if isinstance(ev, UnderlyingTick):
            self.world.on_tick(ev.symbol, ev.ts, ev.price)
        elif isinstance(ev, BookUpdate):
            self.world.on_book(ev.book)
            self._mark(ev)
            self._on_exchange(self.exchange.on_market_event(ev))
            await self._evaluate(ev.book.market_id, ev.ts, t0)
        elif isinstance(ev, PublicTrade):
            self.flow.on_trade(ev)
        elif isinstance(ev, OperatorSignal):
            self.manual.on_operator(ev)
        elif isinstance(ev, MarketOpened):
            self.world.add_market(ev.market)
            self.market_open_ts[ev.market.market_id] = ev.market.open_ts
        elif isinstance(ev, MarketResolved):
            self._on_exchange(self.exchange.on_market_event(ev))
            self._on_resolved(ev)
        elif isinstance(ev, (Fill, OrderClosed)):  # live exchanges report through the stream
            self._on_exchange([ev])

    def _on_exchange(self, updates: list[Fill | OrderClosed]) -> None:
        for u in updates:
            if isinstance(u, Fill):
                self._on_fill(u)
            else:
                self.inflight.pop(u.order_id, None)

    async def _evaluate(self, market_id: str, now: float, t0: float) -> None:
        view = self.world.markets.get(market_id)
        if view is None or view.book is None:
            return
        if view.last_eval_ts is not None and now - view.last_eval_ts < self.cfg.engine.min_eval_interval_s:
            return
        view.last_eval_ts = now

        blocked = self.risk.pre_check(view.market, now, view.last_order_ts)
        if blocked:
            self.stats.blocked[blocked] += 1
            return
        regime = self.regime.at(now)
        if regime.multiplier <= 0:
            self.stats.gated_by_regime += 1
            return

        self.stats.evaluations += 1
        ctx = SignalContext(now, view, self.world)
        outputs = [s.evaluate(ctx) for s in self.signals]
        decision = self.aggregator.combine(outputs, regime, view.book, now)
        if decision is None:
            return
        self.stats.decisions += 1

        if not self.risk.price_ok(decision.entry_price):
            self.stats.blocked["price_band"] += 1
            return
        limit = self.executor.limit_price(decision, view.book, now)
        if limit is None:
            self.stats.blocked["execution_refused"] += 1
            return
        room, reason = self.risk.max_shares(
            view.market, decision.side, limit, self.portfolio, now, self.inflight.values()
        )
        shares = min(kelly_shares(decision, self.portfolio.cash, self.cfg.sizing), room)
        if shares < self.cfg.sizing.min_shares:
            self.stats.blocked[reason if room <= 0 else "size_below_min"] += 1
            return
        order = self.executor.build(decision, limit, shares, market_id, now)

        elapsed_ms = (perf_counter() - t0) * 1000
        self.stats.decision_ms.append(elapsed_ms)
        if elapsed_ms > self.cfg.execution.latency_budget_ms:
            self.stats.dropped_over_budget += 1
            return

        self.inflight[order.order_id] = order
        self._order_meta[order.order_id] = decision
        await self.executor.send(order)
        view.last_order_ts = now
        self.stats.orders += 1

    def _mark(self, ev: BookUpdate) -> None:
        due = self._markouts.get(ev.book.market_id)
        if not due:
            return
        mid = ev.book.mid
        remaining = []
        for due_ts, horizon, rec in due:
            if ev.ts >= due_ts:
                side_mid = mid if rec.side.sign > 0 else 1.0 - mid
                rec.markouts[horizon] = round(side_mid - rec.price, 4)
            else:
                remaining.append((due_ts, horizon, rec))
        self._markouts[ev.book.market_id] = remaining

    def _on_fill(self, fill: Fill) -> None:
        decision = self._order_meta.get(fill.order_id)
        view = self.world.markets.get(fill.market_id)
        rec = self.portfolio.on_fill(
            fill,
            underlying=view.market.underlying if view else "?",
            regime=decision.regime if decision else "unknown",
            contributions=decision.contributions if decision else {},
        )
        self._markouts.setdefault(fill.market_id, []).extend(
            (fill.ts + h, h, rec) for h in self.cfg.engine.markout_horizons_s
        )
        self.stats.fills += 1

    def _on_resolved(self, ev: MarketResolved) -> None:
        # One monitor sample per (signal, regime) per market: fills within a market
        # share an outcome, so counting them separately would overstate the evidence.
        mcfg = self.cfg.monitor
        per_signal: dict[tuple[str, str], list[float]] = {}  # -> [numerator, denominator]
        for rec in self.portfolio.settle(ev.market_id, ev.outcome, ev.ts):
            if mcfg.metric == "markout":
                mk = rec.markouts.get(mcfg.markout_horizon_s)
                num, den = (mk * rec.size, rec.size) if mk is not None else (0.0, 0.0)
            else:
                num, den = rec.pnl or 0.0, rec.cost
            for name, c in rec.contributions.items():
                if c * rec.side.sign > 0 and den > 0:  # this signal argued for the trade
                    agg = per_signal.setdefault((name, rec.regime), [0.0, 0.0])
                    agg[0] += num
                    agg[1] += den
            self._order_meta.pop(rec.order_id, None)
        for (name, regime), (num, den) in per_signal.items():
            self.monitor.record(name, regime, num / den, ev.ts)
        self.flow.forget(ev.market_id)
        self._markouts.pop(ev.market_id, None)  # horizons past resolution stay unmeasured
        self.world.remove_market(ev.market_id)
