from pmbot.config import MonitorConfig
from pmbot.engine import DecayMonitor

CFG = MonitorConfig(window=40, min_samples=20, disable_below=0.0, z_threshold=1.5, cooldown_s=100)


def test_confident_decay_disables_then_probation():
    mon = DecayMonitor(CFG)
    for i in range(20):
        mon.record("lag", "core", -0.3 if i % 2 else -0.1, now=i)
    assert not mon.is_enabled("lag", "core", now=50)
    assert mon.is_enabled("lag", "other_regime", now=50)
    assert mon.is_enabled("lag", "core", now=19 + 100)  # cooldown over
    assert [e.action for e in mon.events] == ["disabled", "reenabled"]


def test_noisy_zero_mean_does_not_disable():
    mon = DecayMonitor(CFG)
    for i in range(40):
        mon.record("lag", "core", 1.0 if i % 2 else -1.05, now=i)
    assert mon.is_enabled("lag", "core", now=41)
    assert mon.events == []
