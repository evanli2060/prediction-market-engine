import random

from pmbot.analysis import score_accounts, to_weights
from pmbot.models import PublicTrade, Side


def test_informed_positive_fader_negative_noise_dropped():
    rng = random.Random(0)
    trades, outcomes = [], {}
    for i in range(300):
        mid = f"m{i}"
        oc = Side.YES if rng.random() < 0.5 else Side.NO
        outcomes[mid] = oc
        good = oc if rng.random() < 0.75 else oc.opposite
        trades.append(PublicTrade(i, mid, "informed", good, 0.5, 100))
        trades.append(PublicTrade(i + 0.5, mid, "fader", good.opposite, 0.5, 100))
        trades.append(PublicTrade(i, mid, "noise", Side.YES if rng.random() < 0.5 else Side.NO, 0.5, 100))
    weights = to_weights(score_accounts(trades, outcomes))
    assert weights["informed"] > 0.3
    assert weights["fader"] < -0.3
    assert "noise" not in weights


def test_one_vote_per_market_and_minimum_history():
    outcomes = {f"m{i}": Side.YES for i in range(5)}
    trades = [PublicTrade(0, f"m{i}", "new", Side.YES, 0.5, 1) for i in range(5) for _ in range(20)]
    s = score_accounts(trades, outcomes, min_markets=30)["new"]
    assert s.markets == 5 and s.weight == 0.0
