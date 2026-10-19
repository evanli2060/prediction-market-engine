"""Run the engine on the simulator and package what happened for the demo page.

Two runs on the same simulated world (identical prices, books and public tape):

* gated:   the default config, trading 09:30-16:00 ET only
* ungated: the same engine trading around the clock

For every market a run traded, the per-second underlying price, the model's
fair value, the book and the bot's orders and fills are kept, so the page can
replay that market. Optionally, a batch of extra seeds shows the spread of
outcomes across simulated worlds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..analysis import collect_history, score_accounts, summarize, to_weights
from ..bot import TradingBot
from ..config import BotConfig
from ..engine import PaperExchange
from ..models import BookUpdate, Event, MarketResolved, OrderRequest
from ..signals import SignalContext
from ..sim import SimConfig, SimulatedEnvironment


@dataclass
class MarketTape:
    market_id: str
    underlying: str
    open_ts: float
    close_ts: float
    strike: float
    under: list[float | None] = field(default_factory=list)
    fair: list[float | None] = field(default_factory=list)
    bid: list[float] = field(default_factory=list)
    ask: list[float] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)


class RecordingBot(TradingBot):
    """A TradingBot that also keeps a per-second tape of every market it touches."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tapes: dict[str, MarketTape] = {}
        self.kept: dict[str, MarketTape] = {}
        self.settlements: list[tuple[float, float]] = []  # (ts, realized pnl of that market)
        send = self.executor.send

        async def recording_send(order: OrderRequest) -> None:
            self._record_order(order)
            await send(order)

        self.executor.send = recording_send  # type: ignore[method-assign]

    def _record_order(self, order: OrderRequest) -> None:
        tape = self.tapes.get(order.market_id)
        decision = self._order_meta.get(order.order_id)
        if tape is None or decision is None:
            return
        tape.orders.append(
            {
                "id": order.order_id,
                "t": round(order.created_ts - tape.open_ts, 3),
                "side": order.side.value,
                "limit": order.limit_price,
                "size": order.size,
                "entry": decision.entry_price,
                "prob": round(decision.prob, 4),
                "edge": round(decision.edge, 4),
                "regime": decision.regime,
                "why": {k: round(v, 4) for k, v in decision.contributions.items()},
            }
        )

    async def on_event(self, ev: Event) -> None:
        await super().on_event(ev)
        if isinstance(ev, BookUpdate):
            view = self.world.markets.get(ev.book.market_id)
            if view is None:
                return
            m = view.market
            tape = self.tapes.get(m.market_id)
            if tape is None:
                tape = self.tapes[m.market_id] = MarketTape(m.market_id, m.underlying, m.open_ts, m.close_ts, m.strike)
            u = self.world.underlyings.get(m.underlying)
            fair = self.lag.fair_prob(SignalContext(ev.ts, view, self.world))
            tape.under.append(u.last_price if u else None)
            tape.fair.append(None if fair is None else round(fair, 4))
            tape.bid.append(ev.book.best_bid)
            tape.ask.append(ev.book.best_ask)
        elif isinstance(ev, MarketResolved):
            tape = self.tapes.pop(ev.market_id, None)
            pnl = sum(t.pnl or 0.0 for t in self.portfolio.trades if t.market_id == ev.market_id)
            if tape is not None and tape.orders:
                self.kept[ev.market_id] = tape
                self.settlements.append((ev.ts, pnl))


def learn_weights(sim_cfg: SimConfig, seed: int, warmup_days: float) -> dict[str, float]:
    start = datetime.fromisoformat(sim_cfg.start) - timedelta(days=warmup_days)
    warm = SimConfig(**{**sim_cfg.__dict__, "start": start.isoformat(), "days": warmup_days, "macro_events": []})
    env = SimulatedEnvironment(warm, seed=seed + 1000)
    trades, outcomes = asyncio.run(collect_history(env.events()))
    return to_weights(score_accounts(trades, outcomes))


def _setup(seed: int, days: float, gated: bool, warmup_days: float) -> tuple[BotConfig, SimulatedEnvironment]:
    sim_cfg = SimConfig(days=days)
    cfg = BotConfig()
    cfg.regime.enabled = gated
    if warmup_days > 0:
        cfg.flow.account_weights = learn_weights(sim_cfg, seed, warmup_days)
    return cfg, SimulatedEnvironment(sim_cfg, seed=seed)


def run_recorded(
    seed: int, days: float, gated: bool, latency: float, warmup_days: float
) -> tuple[RecordingBot, SimulatedEnvironment]:
    cfg, env = _setup(seed, days, gated, warmup_days)
    bot = RecordingBot(cfg, env, PaperExchange(latency_s=latency, fee_rate=cfg.execution.fee_rate))
    asyncio.run(bot.run())
    return bot, env


def run_plain(seed: int, days: float, gated: bool, latency: float, warmup_days: float) -> TradingBot:
    cfg, env = _setup(seed, days, gated, warmup_days)
    bot = TradingBot(cfg, env, PaperExchange(latency_s=latency, fee_rate=cfg.execution.fee_rate))
    asyncio.run(bot.run())
    return bot


def _seed_row(args: tuple[int, float, float, float]) -> dict:
    seed, days, latency, warmup = args
    row: dict = {"seed": seed}
    for label, gated in (("gated", True), ("ungated", False)):
        s = summarize(run_plain(seed, days, gated, latency, warmup))
        row[label] = {
            "ret": s["total"]["ret_per_dollar"],
            "pnl": s["total"]["pnl"],
            "fills": s["total"]["trades"],
            "markets": s["markets_traded"],
            "markout_c": s["total"]["markout_c"],
            "by_regime": {
                k: {"ret": v["ret_per_dollar"], "markout_c": v["markout_c"], "fills": v["trades"]}
                for k, v in s["by_regime"].items()
            },
            "disabled": sorted(
                {f"{e['signal']}@{e['regime']}" for e in s["monitor_events"] if e["action"] == "disabled"}
            ),
        }
    return row


def seed_batch(n: int, days: float, latency: float, warmup: float, log: Callable[[str], None]) -> list[dict]:
    jobs = [(s, days, latency, warmup) for s in range(1, n + 1)]
    rows: list[dict] = []
    with ProcessPoolExecutor() as pool:
        for row in pool.map(_seed_row, jobs):
            rows.append(row)
            log(
                f"  seed {row['seed']:>2}: ret/$ gated {row['gated']['ret']:+.3f}  ungated {row['ungated']['ret']:+.3f}"
            )
    return rows


# --------------------------------------------------------------------------- packaging


def _label_time(ts: float, env: SimulatedEnvironment) -> str:
    return datetime.fromtimestamp(ts, env.tz).strftime("%a %H:%M")


def _market_payload(tape: MarketTape, bot: RecordingBot, env: SimulatedEnvironment, run_label: str) -> dict:
    trades = [t for t in bot.portfolio.trades if t.market_id == tape.market_id]
    pnl = sum(t.pnl or 0.0 for t in trades)
    cost = sum(t.cost for t in trades)
    close_px = tape.under[-1] if tape.under else None
    k = int(tape.market_id.rsplit("-", 1)[1])
    outcome = env.outcome(tape.underlying, k * env.cfg.market_len_s, (k + 1) * env.cfg.market_len_s)
    close_px = env.paths[tape.underlying][(k + 1) * env.cfg.market_len_s]
    signals = sorted({name for o in tape.orders for name, c in o["why"].items() if c})
    return {
        "id": tape.market_id,
        "run": run_label,
        "underlying": tape.underlying,
        "opens": _label_time(tape.open_ts, env),
        "regime": tape.orders[0]["regime"] if tape.orders else "",
        "strike": tape.strike,
        "close": close_px,
        "outcome": outcome.value,
        "pnl": round(pnl, 2),
        "cost": round(cost, 2),
        "signals": signals,
        "mk5_c": (
            round(
                100
                * sum(t.markouts[5] * t.size for t in trades if 5 in t.markouts)
                / sum(t.size for t in trades if 5 in t.markouts),
                2,
            )
            if any(5 in t.markouts for t in trades)
            else None
        ),
        "under": tape.under,
        "fair": tape.fair,
        "bid": tape.bid,
        "ask": tape.ask,
        "orders": tape.orders,
        "fills": [
            {
                "t": round(t.ts - tape.open_ts, 3),
                "order": t.order_id,
                "side": t.side.value,
                "price": t.price,
                "size": t.size,
                "pnl": round(t.pnl or 0.0, 2),
                "markouts": {str(h): m for h, m in t.markouts.items()},
            }
            for t in trades
        ],
    }


def _market_stats(bot: RecordingBot, regime: str) -> dict[str, dict[str, float]]:
    """Per traded market in `regime`: P&L, size-weighted 5 s markout and average fill price."""
    acc: dict[str, dict[str, float]] = {}
    for t in bot.portfolio.trades:
        if t.regime != regime or t.pnl is None:
            continue
        a = acc.setdefault(t.market_id, {"pnl": 0.0, "mk": 0.0, "mk_size": 0.0, "px": 0.0, "size": 0.0})
        a["pnl"] += t.pnl
        a["px"] += t.price * t.size
        a["size"] += t.size
        if 5 in t.markouts:
            a["mk"] += t.markouts[5] * t.size
            a["mk_size"] += t.size
    return {
        m: {"pnl": a["pnl"], "mk": a["mk"] / a["mk_size"] if a["mk_size"] else 0.0, "px": a["px"] / a["size"]}
        for m, a in acc.items()
        if a["size"]
    }


def _pick_showcase(bot: RecordingBot, regime: str, clear: int, by_pnl: int, worst_markout: bool) -> list[str]:
    """Markets that show the mechanism clearly, then a few chosen by P&L so luck is visible too.

    Clear examples are lag-signal trades at mid-range prices with a typical-sized markout
    (1.5-8 cents, positive for core, negative for the off-hours trap). Larger markouts are
    usually a late-window jump, which is a different story.
    """
    stats = _market_stats(bot, regime)

    def lag_traded(m: str) -> bool:
        return any(o["why"].get("lag_momentum", 0) for o in bot.kept[m].orders)

    sign = -1.0 if worst_markout else 1.0
    clear_ok = [
        m for m, v in stats.items() if 0.25 <= v["px"] <= 0.75 and 0.015 <= sign * v["mk"] <= 0.08 and lag_traded(m)
    ]
    ranked = sorted(clear_ok, key=lambda m: sign * stats[m]["mk"], reverse=True)[:clear]
    winners = [m for m in ranked if stats[m]["pnl"] > 0]
    picks = winners[:1] + [m for m in ranked if m not in winners[:1]]  # lead with one that also won
    by = sorted(stats, key=lambda m: stats[m]["pnl"])
    picks += (by[::-1][:by_pnl] + by[:1]) if worst_markout else by[:by_pnl]  # off-hours: best and worst
    for name in ("cross_market", "order_flow", "manual"):
        if any(o["why"].get(name, 0) for m in picks for o in bot.kept[m].orders):
            continue
        for m in sorted(clear_ok or stats, key=lambda m: -sign * stats[m]["mk"]):
            if any(o["why"].get(name, 0) for o in bot.kept[m].orders):
                picks.append(m)
                break
    seen: set[str] = set()
    return [m for m in picks if not (m in seen or seen.add(m))]


def _pick_tour(bot: RecordingBot) -> tuple[str, int] | None:
    """One clean example for the page's guided walkthrough.

    A market-hours YES buy where the gap to the book was clear (3c+), the 5 s
    markout was typical (2-8c), and both the trade and the market made money, so a
    first-time viewer sees the mechanism without having to untangle luck as well.
    """
    by_order: dict[int, list] = {}
    for tr in bot.portfolio.trades:
        by_order.setdefault(tr.order_id, []).append(tr)
    best: tuple[float, str, int] | None = None
    for mid, tape in bot.kept.items():
        market_pnl = sum(t.pnl or 0.0 for t in bot.portfolio.trades if t.market_id == mid)
        if market_pnl <= 0:
            continue
        for o in tape.orders:
            fills = by_order.get(o["id"])
            if o["regime"] != "core" or o["side"] != "YES" or not fills:
                continue
            i = int(o["t"])
            fair, mk = tape.fair[i], fills[0].markouts.get(5)
            if fair is None or mk is None or o["t"] + 7 > len(tape.fair) - 1:
                continue
            gap = fair - tape.ask[i]
            if gap < 0.03 or not 0.02 <= mk <= 0.08 or sum(t.pnl or 0.0 for t in fills) <= 0:
                continue
            if best is None or mk > best[0]:
                best = (mk, mid, o["id"])
    return (best[1], best[2]) if best else None


def _equity(bot: RecordingBot, env: SimulatedEnvironment) -> list[list[float]]:
    pts, cum = [[0.0, 0.0]], 0.0
    for ts, pnl in sorted(bot.settlements):
        cum += pnl
        pts.append([round((ts - env.t0) / 3600, 4), round(cum, 2)])
    pts.append([round((env.n_steps - 1) / 3600, 4), round(cum, 2)])
    return pts


def _half_hours(bot: TradingBot, env: SimulatedEnvironment) -> list[dict]:
    """Markout and P&L per half-hour of the (local) day, so regime boundaries line up."""
    groups: dict[str, dict] = {}
    for t in bot.portfolio.trades:
        dt = datetime.fromtimestamp(t.ts, env.tz)
        key = f"{dt.hour:02d}:{dt.minute // 30 * 30:02d}"
        g = groups.setdefault(key, {"slot": key, "regime": t.regime, "fills": 0, "size": 0.0, "mk5": 0.0, "pnl": 0.0})
        g["fills"] += 1
        g["pnl"] += t.pnl or 0.0
        if 5 in t.markouts:
            g["size"] += t.size
            g["mk5"] += t.markouts[5] * t.size
    out = []
    for key in sorted(groups):
        g = groups[key]
        out.append(
            {
                "slot": key,
                "regime": g["regime"],
                "fills": g["fills"],
                "pnl": round(g["pnl"], 2),
                "mk5_c": round(100 * g["mk5"] / g["size"], 2) if g["size"] else None,
            }
        )
    return out


def build(
    seed: int = 10,
    days: float = 2.0,
    latency: float = 0.8,
    warmup_days: float = 1.0,
    seeds: int = 0,
    log: Callable[[str], None] = print,
) -> dict:
    log(f"Simulating {days:g} days (seed {seed}), gated run...")
    gated, env = run_recorded(seed, days, True, latency, warmup_days)
    log("Same world, ungated run...")
    ungated, _ = run_recorded(seed, days, False, latency, warmup_days)

    # core: the 3 clearest lag captures, then the 2 biggest losers (markouts don't make every trade win)
    # off-hours: the 3 clearest traps, then the biggest winner (P&L can look fine on a bad trade)
    tour = _pick_tour(gated)
    core_ids = _pick_showcase(gated, "core", clear=3, by_pnl=2, worst_markout=False)
    if tour and tour[0] not in core_ids:
        core_ids.insert(0, tour[0])
    showcase = [_market_payload(gated.kept[m], gated, env, "gated") for m in core_ids] + [
        _market_payload(ungated.kept[m], ungated, env, "ungated")
        for m in _pick_showcase(ungated, "off_hours", clear=3, by_pnl=1, worst_markout=True)
    ]

    lo, hi = env.cfg.core_hours
    core_bands = []
    day0 = datetime.fromtimestamp(env.t0, env.tz)
    for d in range(int(days) + 1):
        day = day0 + timedelta(days=d)
        if day.weekday() >= 5:
            continue
        for a, b in [(lo, hi)]:
            start = day.replace(hour=int(a[:2]), minute=int(a[3:]))
            end = day.replace(hour=int(b[:2]), minute=int(b[3:]))
            s, e = (start.timestamp() - env.t0) / 3600, (end.timestamp() - env.t0) / 3600
            if s < days * 24:
                core_bands.append([round(s, 4), round(min(e, days * 24), 4)])

    data = {
        "meta": {
            "seed": seed,
            "days": days,
            "latency_s": latency,
            "start": f"{day0:%a %b} {day0.day}, {day0.year}",
            "hours": round((env.n_steps - 1) / 3600, 4),
            "start_weekday": day0.strftime("%a"),
            "core_hours": [lo, hi],
            "market_len_s": env.cfg.market_len_s,
            "generated": datetime.now().strftime("%Y-%m-%d"),
        },
        "runs": {
            "gated": {"summary": summarize(gated), "equity": _equity(gated, env)},
            "ungated": {
                "summary": summarize(ungated),
                "equity": _equity(ungated, env),
                "half_hours": _half_hours(ungated, env),
            },
        },
        "core_bands": core_bands,
        "markets": showcase,
        "tour": {"market": tour[0], "order": tour[1]} if tour else None,
        "seeds": [],
    }
    if seeds:
        log(f"Seed batch: {seeds} worlds x 2 configs...")
        data["seeds"] = seed_batch(seeds, days, latency, warmup_days, log)
    return data
