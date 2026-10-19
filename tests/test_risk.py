from pmbot.config import RiskConfig, SizingConfig
from pmbot.engine import Decision, Portfolio, RiskManager, kelly_shares
from pmbot.models import Fill, OrderRequest, Side

from helpers import MON_10AM, market


def decision(side=Side.YES, prob=0.70, entry=0.55):
    return Decision(side, prob, entry, prob - entry, 0.5, "core", {})


def test_kelly_zero_without_edge():
    assert kelly_shares(decision(prob=0.55, entry=0.55), 1000, SizingConfig()) == 0


def test_kelly_capped_by_max_stake():
    shares = kelly_shares(decision(prob=0.95, entry=0.50), 1_000_000, SizingConfig(max_stake=15))
    assert shares * 0.50 <= 15


def test_pre_check_timing():
    risk = RiskManager(RiskConfig(entry_delay_s=300, entry_cutoff_s=45, min_order_interval_s=10))
    m = market(open_ts=MON_10AM)
    assert risk.pre_check(m, MON_10AM + 60, None) == "too_early"
    assert risk.pre_check(m, MON_10AM + 880, None) == "inside_cutoff"
    assert risk.pre_check(m, MON_10AM + 400, MON_10AM + 395) == "cooldown"
    assert risk.pre_check(m, MON_10AM + 400, None) is None


def test_opposite_side_blocked_and_market_cap():
    risk = RiskManager(RiskConfig(max_position_per_market=40))
    pf = Portfolio(1000)
    m = market()
    pf.on_fill(Fill(MON_10AM, 1, m.market_id, Side.YES, 0.50, 60), underlying="BTC", regime="core", contributions={})
    assert risk.max_shares(m, Side.NO, 0.5, pf, MON_10AM) == (0.0, "opposite_position")
    shares, reason = risk.max_shares(m, Side.YES, 0.5, pf, MON_10AM)
    assert reason == "ok" and shares == 20  # $10 of room at $0.50


def test_daily_loss_limit():
    risk = RiskManager(RiskConfig(daily_loss_limit=10))
    pf = Portfolio(1000)
    m = market()
    pf.on_fill(Fill(MON_10AM, 1, m.market_id, Side.YES, 0.50, 40), underlying="BTC", regime="core", contributions={})
    pf.settle(m.market_id, Side.NO, MON_10AM + 600)
    assert risk.max_shares(market("M2"), Side.YES, 0.5, pf, MON_10AM + 700)[1] == "daily_loss_limit"


def test_settlement_pnl():
    pf = Portfolio(100)
    pf.on_fill(Fill(MON_10AM, 1, "M", Side.NO, 0.40, 10, fee=0.1), underlying="BTC", regime="core", contributions={})
    (rec,) = pf.settle("M", Side.NO, MON_10AM + 600)
    assert abs(rec.pnl - (10 - 4.1)) < 1e-9
    assert abs(pf.cash - (100 - 4.1 + 10)) < 1e-9


def test_inflight_opposite_order_blocks_and_reserves_room():
    risk = RiskManager(RiskConfig(max_position_per_market=40, max_total_exposure=300))
    pf = Portfolio(1000)
    m = market()
    inflight = [OrderRequest(1, m.market_id, Side.YES, 0.50, 40, MON_10AM)]  # $20 worst case
    assert risk.max_shares(m, Side.NO, 0.5, pf, MON_10AM, inflight) == (0.0, "opposite_position")
    assert risk.max_shares(m, Side.YES, 0.5, pf, MON_10AM, inflight) == (40.0, "ok")  # $20 left
    other = market("M2")
    assert risk.max_shares(other, Side.NO, 0.5, pf, MON_10AM, inflight) == (80.0, "ok")


def test_room_is_measured_at_the_limit_price():
    risk = RiskManager(RiskConfig(max_position_per_market=40))
    shares, _ = risk.max_shares(market(), Side.YES, 0.57, Portfolio(1000), MON_10AM)
    assert shares * 0.57 <= 40 < (shares + 1) * 0.57


def test_unexpected_opposite_fill_is_accounted_not_fatal():
    pf = Portfolio(100)
    pf.on_fill(Fill(MON_10AM, 1, "M", Side.YES, 0.60, 10), underlying="BTC", regime="core", contributions={})
    pf.on_fill(Fill(MON_10AM, 2, "M", Side.NO, 0.45, 10), underlying="BTC", regime="core", contributions={})
    assert pf.exposure() == 10.5
    recs = pf.settle("M", Side.YES, MON_10AM + 600)
    assert sorted(round(r.pnl, 2) for r in recs) == [-4.5, 4.0]
    assert abs(pf.cash - (100 - 10.5 + 10)) < 1e-9
