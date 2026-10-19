"""Time-of-day regime gate.

Market behaviour is not stationary across the day: who is active, how deep the
books are and how often prices get pushed around near resolution all change by
hour. The gate labels every moment with a regime and a size multiplier. A
multiplier of 0 means "don't trade here at all".

Windows are configuration, not code, because the right ones drift as other
participants adapt. The decay monitor (engine.monitor) is what tells you when to
move them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import RegimeConfig


@dataclass(frozen=True)
class RegimeState:
    label: str
    multiplier: float


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class RegimeGate:
    def __init__(self, cfg: RegimeConfig) -> None:
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.tz)
        self._windows = [(_minutes(w.start), _minutes(w.end), w) for w in cfg.windows]
        self._cache_key: int | None = None
        self._cache_val: RegimeState | None = None

    def at(self, ts: float) -> RegimeState:
        key = int(ts // 60)
        if key == self._cache_key and self._cache_val is not None:
            return self._cache_val
        state = self._compute(ts)
        self._cache_key, self._cache_val = key, state
        return state

    def _compute(self, ts: float) -> RegimeState:
        dt = datetime.fromtimestamp(ts, self.tz)
        if dt.weekday() >= 5:
            state = RegimeState("weekend", self.cfg.weekend_multiplier)
        else:
            minute = dt.hour * 60 + dt.minute
            state = RegimeState(self.cfg.default_label, self.cfg.default_multiplier)
            for start, end, w in self._windows:
                if start <= minute < end:
                    state = RegimeState(w.label, w.multiplier)
                    break
        if not self.cfg.enabled:
            return RegimeState(state.label, 1.0)
        return state
