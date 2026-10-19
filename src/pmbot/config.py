"""Configuration objects.

Every numeric default in this file is a PLACEHOLDER picked so the simulator runs
end to end. None of them are the parameters of the production system.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LagMomentumConfig:
    vol_window_s: float = 1800.0  # realized-vol lookback
    vol_estimator: str = "bipower"  # "rv" (sum of squares) or "bipower" (jump-robust)
    lookback_s: float = 60.0  # momentum lookback
    momentum_weight: float = 0.15  # shrink raw momentum toward zero
    momentum_horizon_s: float = 120.0  # how far forward momentum is assumed to persist
    min_edge: float = 0.03  # fair-vs-book gap needed to emit a score
    edge_scale: float = 0.15  # edge that maps to a full-strength score
    max_tick_age_s: float = 3.0  # ignore if the underlying feed is stale


@dataclass
class CrossMarketConfig:
    leader_of: dict[str, str] = field(default_factory=lambda: {"ETH": "BTC"})
    leader_threshold: float = 0.75  # leader must be at least this one-sided
    min_gap: float = 0.08  # follower must lag the leader by at least this much
    gap_scale: float = 0.25
    agreement_rate: float = 0.8  # estimate offline from resolved leader/follower pairs


@dataclass
class OrderFlowConfig:
    # account -> weight. Positive = follow, negative = fade. Learned, never hand-typed.
    account_weights: dict[str, float] = field(default_factory=dict)
    decay_s: float = 120.0
    flow_scale: float = 300.0
    min_abs_flow: float = 25.0


@dataclass
class ManualConfig:
    applies_to: tuple[str, ...] = ()  # empty = every underlying


@dataclass
class RegimeWindow:
    start: str  # "HH:MM" local time, inclusive
    end: str  # "HH:MM" local time, exclusive
    multiplier: float
    label: str


@dataclass
class RegimeConfig:
    enabled: bool = True  # False = never gate, but still label trades by regime
    tz: str = "America/New_York"
    windows: list[RegimeWindow] = field(default_factory=lambda: [RegimeWindow("09:30", "16:00", 1.0, "core")])
    default_label: str = "off_hours"
    default_multiplier: float = 0.0
    weekend_multiplier: float = 0.0


@dataclass
class AggregatorConfig:
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "lag_momentum": 0.45,
            "cross_market": 0.20,
            "order_flow": 0.20,
            "manual": 0.15,
        }
    )
    max_prob_shift: float = 0.30  # combined score of 1.0 moves our estimate this far from mid
    min_edge: float = 0.03
    disagreement_penalty: float = 0.6


@dataclass
class SizingConfig:
    kelly_fraction: float = 0.2
    max_stake: float = 15.0
    min_shares: float = 5.0


@dataclass
class RiskConfig:
    starting_cash: float = 1_000.0
    max_position_per_market: float = 40.0
    max_total_exposure: float = 300.0
    daily_loss_limit: float = 400.0  # ~2 sd of a normal day at these sizes
    entry_delay_s: float = 300.0  # no entries early in the window (see report: by minute in window)
    entry_cutoff_s: float = 45.0  # no entries this close to resolution
    min_order_interval_s: float = 10.0  # per-market cooldown
    # Only buy contracts priced inside this band. In the tails a small vol error is a
    # large probability error, and a 5c contract turns a $15 stake into a $300 lottery ticket.
    min_entry_price: float = 0.10
    max_entry_price: float = 0.90


@dataclass
class ExecutionConfig:
    max_slippage: float = 0.02
    min_edge_at_limit: float = 0.01  # never let slippage eat the whole edge
    max_book_age_s: float = 2.0
    latency_budget_ms: float = 250.0  # drop decisions that took longer than this
    fee_rate: float = 0.0  # set to the venue's current fee schedule


@dataclass
class MonitorConfig:
    # What a signal is judged on, one sample per resolved market:
    #   "markout": size-weighted book move in our favour `markout_horizon_s` after each fill.
    #              Measures whether the signal finds stale prices, without outcome noise.
    #   "pnl":     realized return per $ risked. What you ultimately care about, but a
    #              15-minute binary outcome is so noisy that it takes far longer to judge.
    metric: str = "markout"
    markout_horizon_s: int = 5
    window: int = 80
    min_samples: int = 30  # resolved markets, not fills
    disable_below: float = 0.0
    z_threshold: float = 2.0  # disable only when confidently below the floor
    cooldown_s: float = 6 * 3600


@dataclass
class EngineConfig:
    min_eval_interval_s: float = 1.0
    # Mark every fill against the book this many seconds later. Markouts measure whether
    # we bought before the book moved, separately from how the binary outcome landed.
    markout_horizons_s: tuple[int, ...] = (5, 30)


@dataclass
class BotConfig:
    lag: LagMomentumConfig = field(default_factory=LagMomentumConfig)
    cross: CrossMarketConfig = field(default_factory=CrossMarketConfig)
    flow: OrderFlowConfig = field(default_factory=OrderFlowConfig)
    manual: ManualConfig = field(default_factory=ManualConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    aggregator: AggregatorConfig = field(default_factory=AggregatorConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
