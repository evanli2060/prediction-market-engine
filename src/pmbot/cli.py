"""Run the engine against the simulator or a recorded stream.

python -m pmbot.cli                       # 2 simulated days, regime gating on
python -m pmbot.cli --no-regime           # trade every hour; watch the monitor react
python -m pmbot.cli --record run.jsonl    # save the stream
python -m pmbot.cli --replay run.jsonl    # re-run the engine on a saved stream
python -m pmbot.cli --seeds 10            # distribution across 10 simulated worlds
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from .analysis import collect_history, format_report, score_accounts, summarize, to_weights
from .bot import TradingBot
from .config import BotConfig
from .engine import PaperExchange
from .io import RecordingSource, ReplaySource
from .sim import SimConfig, SimulatedEnvironment


def learn_account_weights(sim_cfg: SimConfig, days: float, seed: int, verbose: bool) -> dict[str, float]:
    """Score public accounts on a warm-up period that precedes the evaluation run."""
    start = datetime.fromisoformat(sim_cfg.start) - timedelta(days=days)
    warm_cfg = SimConfig(**{**sim_cfg.__dict__, "start": start.isoformat(), "days": days, "macro_events": []})
    env = SimulatedEnvironment(warm_cfg, seed=seed + 1000)
    trades, outcomes = asyncio.run(collect_history(env.events()))
    scores = score_accounts(trades, outcomes, is_off_hours=lambda ts: not env.is_core(int(ts - env.t0)))
    weights = to_weights(scores)
    if verbose:
        print(f"Account scoring on {days:g} warm-up day(s): {len(outcomes)} markets, {len(trades):,} public trades")
        for s in sorted(scores.values(), key=lambda s: -abs(s.weight))[:6]:
            print(f"  {s.account}  markets={s.markets:<4} acc={s.accuracy:.3f}  weight={s.weight:+.2f}")
    return weights


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="python -m pmbot.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--days", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=10)
    ap.add_argument("--no-regime", action="store_true", help="disable time-of-day gating")
    ap.add_argument("--warmup-days", type=float, default=1.0, help="0 = skip account learning")
    ap.add_argument("--latency", type=float, default=0.8, help="simulated order latency, seconds")
    ap.add_argument("--record", help="write the event stream to this JSONL file")
    ap.add_argument(
        "--replay",
        help="run on a recorded JSONL stream instead of the simulator "
        "(account weights still come from the --seed warm-up, so pass the seed you recorded with)",
    )
    ap.add_argument("--json", help="write the summary to this file")
    ap.add_argument("--seeds", type=int, help="run seeds 1..N and print one line per seed")
    args = ap.parse_args(argv)

    if args.replay and not Path(args.replay).is_file():
        ap.error(f"replay file not found: {args.replay}")
    if args.seeds and (args.record or args.replay):
        ap.error("--seeds runs many simulations; it can't be combined with --record or --replay")
    if args.days <= 0 or args.latency < 0 or args.warmup_days < 0:
        ap.error("--days must be > 0; --latency and --warmup-days must be >= 0")

    if args.seeds:
        run_batch(args)
        return

    bot = build_and_run(args, args.seed, verbose=True)
    summary = summarize(bot)
    print(format_report(summary))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(summary, fh, indent=2)


def build_and_run(args, seed: int, verbose: bool) -> TradingBot:
    cfg = BotConfig()
    if args.no_regime:
        cfg.regime.enabled = False

    sim_cfg = SimConfig(days=args.days)
    if args.warmup_days > 0:
        cfg.flow.account_weights = learn_account_weights(sim_cfg, args.warmup_days, seed, verbose=verbose)

    source = ReplaySource(args.replay) if args.replay else SimulatedEnvironment(sim_cfg, seed=seed)
    if args.record:
        source = RecordingSource(source, args.record)

    bot = TradingBot(cfg, source, PaperExchange(latency_s=args.latency, fee_rate=cfg.execution.fee_rate))
    asyncio.run(bot.run())
    return bot


def run_batch(args) -> None:
    print(f"{'seed':>4} {'fills':>6} {'mkts':>5} {'pnl':>9} {'ret/$':>7}   by regime (ret/$)")
    rets = []
    for seed in range(1, args.seeds + 1):
        s = summarize(build_and_run(args, seed, verbose=False))
        t = s["total"]
        rets.append(t["ret_per_dollar"])
        regimes = "  ".join(f"{k}={v['ret_per_dollar']:+.3f}" for k, v in s["by_regime"].items())
        print(
            f"{seed:>4} {t['trades']:>6} {s['markets_traded']:>5} {t['pnl']:>9.2f} "
            f"{t['ret_per_dollar']:>+7.3f}   {regimes}"
        )
    mean = sum(rets) / len(rets)
    print(f"mean ret/$ {mean:+.3f} | positive seeds {sum(r > 0 for r in rets)}/{len(rets)}")


if __name__ == "__main__":
    main()
