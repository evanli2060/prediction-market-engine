from pmbot.config import AggregatorConfig, MonitorConfig
from pmbot.engine import Aggregator, DecayMonitor, RegimeState
from pmbot.models import Side

from helpers import MON_10AM, book, out

CORE = RegimeState("core", 1.0)


def test_strong_yes_signal_buys_yes():
    agg = Aggregator(AggregatorConfig())
    d = agg.combine([out("lag_momentum", 1.0)], CORE, book(0.49, 0.51), MON_10AM)
    assert d is not None and d.side is Side.YES
    assert d.entry_price == 0.51
    assert d.edge >= AggregatorConfig().min_edge


def test_strong_no_signal_buys_no_at_implied_price():
    agg = Aggregator(AggregatorConfig())
    d = agg.combine([out("lag_momentum", -1.0)], CORE, book(0.49, 0.51), MON_10AM)
    assert d is not None and d.side is Side.NO
    assert d.entry_price == 0.51  # 1 - best YES bid


def test_veto_blocks():
    agg = Aggregator(AggregatorConfig())
    outputs = [out("lag_momentum", 1.0), out("order_flow", 0.0, veto=True)]
    assert agg.combine(outputs, CORE, book(0.49, 0.51), MON_10AM) is None


def test_disagreement_shrinks_score():
    agg = Aggregator(AggregatorConfig(min_edge=0.0))
    agree = agg.combine([out("lag_momentum", 1.0), out("order_flow", 0.5)], CORE, book(0.49, 0.51), MON_10AM)
    fight = agg.combine([out("lag_momentum", 1.0), out("order_flow", -0.5)], CORE, book(0.49, 0.51), MON_10AM)
    assert agree.score > fight.score > 0


def test_regime_multiplier_zero_blocks():
    agg = Aggregator(AggregatorConfig())
    assert agg.combine([out("lag_momentum", 1.0)], RegimeState("off", 0.0), book(0.49, 0.51), MON_10AM) is None


def test_weak_signal_does_not_clear_spread():
    agg = Aggregator(AggregatorConfig())
    assert agg.combine([out("lag_momentum", 0.05)], CORE, book(0.49, 0.51), MON_10AM) is None


def test_monitor_disabled_signal_is_ignored():
    mon = DecayMonitor(MonitorConfig(min_samples=5, window=5, z_threshold=1.0))
    for _ in range(5):
        mon.record("lag_momentum", "core", -1.0 + 0.01 * _, MON_10AM)
    agg = Aggregator(AggregatorConfig(), mon)
    assert agg.combine([out("lag_momentum", 1.0)], CORE, book(0.49, 0.51), MON_10AM) is None
