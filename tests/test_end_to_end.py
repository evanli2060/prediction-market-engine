import asyncio

from pmbot.bot import TradingBot
from pmbot.config import BotConfig
from pmbot.engine import PaperExchange
from pmbot.io import RecordingSource, ReplaySource
from pmbot.sim import SimConfig, SimulatedEnvironment

SHORT = dict(start="2026-01-05T09:30:00", days=0.15)  # ~3.5 hours of core session


async def collect(source):
    return [ev async for ev in source.events()]


def run_bot(source):
    bot = TradingBot(BotConfig(), source, PaperExchange(0.8))
    asyncio.run(bot.run())
    return bot


def test_record_then_replay_is_identical(tmp_path):
    path = tmp_path / "run.jsonl"
    recorded = asyncio.run(collect(RecordingSource(SimulatedEnvironment(SimConfig(**SHORT), seed=3), path)))
    replayed = asyncio.run(collect(ReplaySource(path)))
    assert recorded == replayed


def test_bot_is_deterministic_and_trades(tmp_path):
    a = run_bot(SimulatedEnvironment(SimConfig(**SHORT), seed=3))
    b = run_bot(SimulatedEnvironment(SimConfig(**SHORT), seed=3))
    assert a.stats.fills > 0
    assert [(t.order_id, t.price, t.size, t.pnl) for t in a.portfolio.trades] == [
        (t.order_id, t.price, t.size, t.pnl) for t in b.portfolio.trades
    ]


def test_replay_reproduces_live_run(tmp_path):
    path = tmp_path / "run.jsonl"
    live = run_bot(RecordingSource(SimulatedEnvironment(SimConfig(**SHORT), seed=5), path))
    replay = run_bot(ReplaySource(path))
    assert abs(live.portfolio.cash - replay.portfolio.cash) < 1e-9


def test_slow_venue_never_buys_both_sides_or_breaks_caps():
    """Regression: with 15 s order latency the bot used to send a NO order while its YES
    order on the same market was still in flight, then crash on the opposite-side fill."""
    cfg = BotConfig()
    cfg.regime.enabled = False
    env = SimulatedEnvironment(SimConfig(start="2026-01-05T09:30:00", days=0.3), seed=7)
    bot = TradingBot(cfg, env, PaperExchange(latency_s=15))
    asyncio.run(bot.run())
    assert bot.stats.fills > 0
    sides, cost = {}, {}
    for t in bot.portfolio.trades:
        sides.setdefault(t.market_id, set()).add(t.side)
        cost[t.market_id] = cost.get(t.market_id, 0.0) + t.cost
    assert all(len(s) == 1 for s in sides.values())
    assert max(cost.values()) <= cfg.risk.max_position_per_market + 1e-9
    assert not bot.inflight  # every order was closed out
