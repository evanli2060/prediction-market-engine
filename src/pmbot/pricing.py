"""Fair value for 'will the underlying close at or above the strike?' markets."""

from __future__ import annotations

from math import erf, log, sqrt


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def binary_fair_prob(
    spot: float,
    strike: float,
    vol_per_s: float,
    seconds_left: float,
    expected_log_move: float = 0.0,
) -> float:
    """P(S_T >= K) under a driftless lognormal walk, optionally shifted by an expected move.

    `vol_per_s` is the standard deviation of log returns per second.
    `expected_log_move` lets a momentum estimate tilt the distribution.
    """
    if spot <= 0 or strike <= 0:
        return 0.5
    if seconds_left <= 0:
        return 1.0 if spot >= strike else 0.0
    sd = vol_per_s * sqrt(seconds_left)
    if sd <= 0:
        return 1.0 if spot >= strike else 0.0
    return norm_cdf((log(spot / strike) + expected_log_move) / sd)


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x
