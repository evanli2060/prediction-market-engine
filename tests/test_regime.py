from pmbot.config import RegimeConfig
from pmbot.engine import RegimeGate

from helpers import ts


def test_core_window_and_default():
    gate = RegimeGate(RegimeConfig())
    assert gate.at(ts("2026-01-05T10:00:00")).label == "core"
    assert gate.at(ts("2026-01-05T10:00:00")).multiplier == 1.0
    early = gate.at(ts("2026-01-05T08:00:00"))
    assert (early.label, early.multiplier) == ("off_hours", 0.0)


def test_window_end_is_exclusive():
    gate = RegimeGate(RegimeConfig())
    assert gate.at(ts("2026-01-05T15:59:00")).label == "core"
    assert gate.at(ts("2026-01-05T16:00:00")).label == "off_hours"


def test_weekend():
    gate = RegimeGate(RegimeConfig())
    assert gate.at(ts("2026-01-10T12:00:00")).label == "weekend"


def test_disabled_gate_keeps_labels_but_never_blocks():
    gate = RegimeGate(RegimeConfig(enabled=False))
    s = gate.at(ts("2026-01-05T03:00:00"))
    assert (s.label, s.multiplier) == ("off_hours", 1.0)
