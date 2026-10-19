"""Strategy-decay monitor.

Every edge in this market eventually gets competed away, usually without any
announcement. The monitor attributes each settled trade to the signals that
agreed with it, keeps a rolling window of realized return per (signal, regime),
and switches a signal off in a regime once its recent mean is *confidently*
below a floor (one-sided t-test), so a cold streak alone doesn't kill a signal.
After a cooldown it comes back on probation with a fresh window.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import sqrt

from ..config import MonitorConfig


@dataclass(frozen=True)
class MonitorEvent:
    ts: float
    signal: str
    regime: str
    action: str  # "disabled" | "reenabled"
    mean_metric: float
    samples: int


class DecayMonitor:
    def __init__(self, cfg: MonitorConfig) -> None:
        self.cfg = cfg
        self._samples: dict[tuple[str, str], deque[float]] = {}
        self._disabled_until: dict[tuple[str, str], float] = {}
        self.events: list[MonitorEvent] = []

    def is_enabled(self, signal: str, regime: str, now: float) -> bool:
        key = (signal, regime)
        until = self._disabled_until.get(key)
        if until is None:
            return True
        if now >= until:
            del self._disabled_until[key]
            self._samples.pop(key, None)
            self.events.append(MonitorEvent(now, signal, regime, "reenabled", 0.0, 0))
            return True
        return False

    def record(self, signal: str, regime: str, ret: float, now: float) -> None:
        key = (signal, regime)
        if key in self._disabled_until:
            return
        dq = self._samples.setdefault(key, deque(maxlen=self.cfg.window))
        dq.append(ret)
        n = len(dq)
        if n >= self.cfg.min_samples:
            mean = sum(dq) / n
            var = sum((x - mean) ** 2 for x in dq) / (n - 1)
            se = sqrt(var / n) if var > 0 else 1e-9
            if (mean - self.cfg.disable_below) / se < -self.cfg.z_threshold:
                self._disabled_until[key] = now + self.cfg.cooldown_s
                self.events.append(MonitorEvent(now, signal, regime, "disabled", mean, n))

    def rolling_mean(self, signal: str, regime: str) -> float | None:
        dq = self._samples.get((signal, regime))
        return sum(dq) / len(dq) if dq else None
