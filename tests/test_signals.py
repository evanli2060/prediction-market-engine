from pmbot.config import CrossMarketConfig, LagMomentumConfig, ManualConfig, OrderFlowConfig
from pmbot.live import parse_operator_line
from pmbot.models import OperatorSignal, PublicTrade, Side
from pmbot.signals import (
    CrossMarketSignal,
    LagMomentumSignal,
    ManualOverrideSignal,
    OrderFlowSignal,
    SignalContext,
)
from pmbot.state import WorldState

from helpers import MON_10AM, book, market


def world_with_path(prices, start=MON_10AM - 300):
    w = WorldState(vol_window_s=300)
    for i, p in enumerate(prices):
        w.on_tick("BTC", start + i, p)
    return w


def test_lag_signal_sees_underlying_ahead_of_book():
    import random

    rng = random.Random(1)
    prices = [100.0]
    for _ in range(299):
        prices.append(prices[-1] * (1 + rng.gauss(0, 1e-4)))
    prices[-1] = 100.3  # underlying jumps well above strike...
    w = world_with_path(prices)
    m = market(strike=100.0)
    w.add_market(m)
    w.on_book(book(0.49, 0.51))  # ...but the book hasn't moved
    now = MON_10AM - 1
    out = LagMomentumSignal(LagMomentumConfig()).evaluate(SignalContext(now, w.markets["M"], w))
    assert out.score > 0.5 and out.confidence == 1.0


def test_lag_signal_ignores_stale_feed():
    w = world_with_path([100.0] * 300)
    w.add_market(market())
    w.on_book(book(0.49, 0.51))
    out = LagMomentumSignal(LagMomentumConfig()).evaluate(SignalContext(MON_10AM + 60, w.markets["M"], w))
    assert out.score == 0 and "stale" in out.reason


def test_cross_market_leans_follower_toward_leader():
    w = WorldState(300)
    w.add_market(market("B", "BTC"))
    w.add_market(market("E", "ETH"))
    w.on_book(book(0.89, 0.91, "B"))
    w.on_book(book(0.59, 0.61, "E"))
    sig = CrossMarketSignal(CrossMarketConfig())
    assert sig.evaluate(SignalContext(MON_10AM, w.markets["E"], w)).score > 0
    assert sig.evaluate(SignalContext(MON_10AM, w.markets["B"], w)).score == 0  # leader isn't a follower


def test_order_flow_weights_and_decay():
    sig = OrderFlowSignal(OrderFlowConfig(account_weights={"good": 1.0, "fade": -1.0}, decay_s=60))
    w = WorldState(300)
    w.add_market(market())
    sig.on_trade(PublicTrade(MON_10AM, "M", "good", Side.YES, 0.5, 200))
    sig.on_trade(PublicTrade(MON_10AM, "M", "fade", Side.NO, 0.5, 100))  # fading a NO = YES
    sig.on_trade(PublicTrade(MON_10AM, "M", "unknown", Side.NO, 0.5, 10_000))
    now_score = sig.evaluate(SignalContext(MON_10AM, w.markets["M"], w)).score
    later_score = sig.evaluate(SignalContext(MON_10AM + 120, w.markets["M"], w)).score
    assert now_score > later_score > 0


def test_manual_override_expires():
    sig = ManualOverrideSignal(ManualConfig())
    w = WorldState(300)
    w.add_market(market())
    sig.on_operator(OperatorSignal(MON_10AM, -1.0, 120))
    assert sig.evaluate(SignalContext(MON_10AM + 60, w.markets["M"], w)).score == -1.0
    assert sig.evaluate(SignalContext(MON_10AM + 121, w.markets["M"], w)).score == 0.0


def test_operator_line_parsing():
    assert parse_operator_line("+ 90 hawkish", 0).ttl_s == 90
    assert parse_operator_line("-", 0).value == -1.0
    assert parse_operator_line("0", 0).value == 0.0
    assert parse_operator_line("buy", 0) is None
