"""Post-run breakdowns: where the money came from, and where it stopped coming from."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..bot import TradingBot


def _bucket():
    return {"trades": 0, "pnl": 0.0, "cost": 0.0, "wins": 0, "mk": {}}


def _add(b: dict, pnl: float, cost: float, rec=None) -> None:
    b["trades"] += 1
    b["pnl"] += pnl
    b["cost"] += cost
    b["wins"] += pnl > 0
    if rec is not None:
        for h, m in rec.markouts.items():
            acc = b["mk"].setdefault(h, [0.0, 0.0])
            acc[0] += m * rec.size
            acc[1] += rec.size


def _finish(groups: dict) -> dict:
    return {
        k: {
            "trades": v["trades"],
            "pnl": round(v["pnl"], 2),
            "cost": round(v["cost"], 2),
            "ret_per_dollar": round(v["pnl"] / v["cost"], 4) if v["cost"] else 0.0,
            "win_rate": round(v["wins"] / v["trades"], 3) if v["trades"] else 0.0,
            # size-weighted markout, cents per contract
            "markout_c": {str(h): round(100 * s / n, 2) for h, (s, n) in sorted(v["mk"].items()) if n},
        }
        for k, v in sorted(groups.items())
    }


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def summarize(bot: TradingBot) -> dict:
    pf, tz = bot.portfolio, bot.portfolio.tz
    settled = [t for t in pf.trades if t.pnl is not None]
    total, by_regime, by_day, by_hour, by_under, by_signal, by_minute = (
        _bucket(),
        defaultdict(_bucket),
        defaultdict(_bucket),
        defaultdict(_bucket),
        defaultdict(_bucket),
        defaultdict(_bucket),
        defaultdict(_bucket),
    )
    open_ts = getattr(bot, "market_open_ts", {})
    for t in settled:
        pnl = t.pnl or 0.0
        dt = datetime.fromtimestamp(t.ts, tz)
        if t.market_id in open_ts:
            m0 = int((t.ts - open_ts[t.market_id]) // 180) * 3
            _add(by_minute[f"{m0:02d}-{m0 + 3:02d} min"], pnl, t.cost, t)
        _add(total, pnl, t.cost, t)
        _add(by_regime[t.regime], pnl, t.cost, t)
        _add(by_day[dt.strftime("%Y-%m-%d")], pnl, t.cost, t)
        _add(by_hour[f"{dt.hour:02d}:00"], pnl, t.cost, t)
        _add(by_under[t.underlying], pnl, t.cost, t)
        for name, c in t.contributions.items():
            if c * t.side.sign > 0:
                _add(by_signal[name], pnl, t.cost, t)

    s = bot.stats
    return {
        "total": _finish({"all": total})["all"],
        "ending_cash": round(pf.cash, 2),
        "markets_traded": len({t.market_id for t in settled}),
        "by_day": _finish(by_day),
        "by_regime": _finish(by_regime),
        "by_underlying": _finish(by_under),
        "by_signal_when_agreeing": _finish(by_signal),
        "by_minute_in_window": _finish(by_minute),
        "by_hour": _finish(by_hour),
        "engine": {
            "events": s.events,
            "evaluations": s.evaluations,
            "decisions": s.decisions,
            "orders": s.orders,
            "fills": s.fills,
            "unfilled_orders": getattr(bot.exchange, "unfilled", None),
            "gated_by_regime": s.gated_by_regime,
            "dropped_over_latency_budget": s.dropped_over_budget,
            "decision_ms_p50": round(median(s.decision_ms), 3) if s.decision_ms else 0.0,
            "decision_ms_p99": round(_pct(s.decision_ms, 0.99), 3),
            "blocked": dict(s.blocked.most_common()),
        },
        "monitor_events": [
            {
                "time": datetime.fromtimestamp(e.ts, tz).strftime("%a %H:%M"),
                "signal": e.signal,
                "regime": e.regime,
                "action": e.action,
                "mean_metric": round(e.mean_metric, 4),
                "samples": e.samples,
            }
            for e in bot.monitor.events
        ],
    }


def _table(title: str, rows: dict) -> list[str]:
    out = [f"\n{title}", f"  {'':<16}{'trades':>8}{'pnl':>11}{'ret/$':>9}{'win%':>7}{'mk5s':>9}{'mk30s':>9}"]
    for k, v in rows.items():
        mk = v.get("markout_c", {})
        out.append(
            f"  {k:<16}{v['trades']:>8}{v['pnl']:>11.2f}{v['ret_per_dollar']:>9.3f}{v['win_rate'] * 100:>6.1f}%"
            f"{_c(mk.get('5')):>9}{_c(mk.get('30')):>9}"
        )
    return out


def _c(x: float | None) -> str:
    return "" if x is None else f"{x:+.2f}c"


def format_report(summary: dict) -> str:
    t, e = summary["total"], summary["engine"]
    lines = [
        "=" * 56,
        f"PnL ${t['pnl']:.2f} | {t['trades']} fills | {summary['markets_traded']} markets | "
        f"ret/$ {t['ret_per_dollar']:.3f} | win {t['win_rate'] * 100:.1f}%",
        "=" * 56,
        "  mk5s / mk30s: book mid on our side minus fill price, 5 s / 30 s after the fill,",
        "  in cents per contract. Positive means the book moved our way after we bought.",
    ]
    lines += _table("By day", summary["by_day"])
    lines += _table("By regime", summary["by_regime"])
    lines += _table("By underlying", summary["by_underlying"])
    lines += _table("By minutes into window", summary["by_minute_in_window"])
    lines += _table("By signal (trades it argued for)", summary["by_signal_when_agreeing"])
    lines.append("\nEngine")
    lines.append(
        f"  evaluations {e['evaluations']:,} | decisions {e['decisions']:,} | orders {e['orders']:,} | "
        f"fills {e['fills']:,} | unfilled {e['unfilled_orders']}"
    )
    lines.append(
        f"  decision latency p50 {e['decision_ms_p50']} ms, p99 {e['decision_ms_p99']} ms | "
        f"over budget {e['dropped_over_latency_budget']} | regime-gated evals {e['gated_by_regime']:,}"
    )
    if e["blocked"]:
        lines.append("  blocked: " + ", ".join(f"{k}={v:,}" for k, v in e["blocked"].items()))
    if e["fills"] == 0:
        why = (
            "every evaluation fell outside an enabled regime (the sim starts at midnight; "
            "run at least ~0.5 days, or pass --no-regime)"
            if e["gated_by_regime"] and not e["evaluations"]
            else "no signal combination cleared the minimum edge"
        )
        lines.append(f"\n  No trades: {why}.")
    if summary["monitor_events"]:
        lines.append("\nDecay monitor")
        for m in summary["monitor_events"]:
            lines.append(
                f"  {m['time']}  {m['action']:<10} {m['signal']} in {m['regime']}"
                + (f"  (mean {m['mean_metric']:+.4f} over {m['samples']} markets)" if m["action"] == "disabled" else "")
            )
    return "\n".join(lines)
