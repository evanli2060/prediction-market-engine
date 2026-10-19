import pytest

from pmbot.cli import main


def test_missing_replay_file_is_a_clean_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--replay", "does-not-exist.jsonl"])
    assert exc.value.code == 2
    assert "replay file not found" in capsys.readouterr().err


def test_seeds_cannot_be_combined_with_record():
    with pytest.raises(SystemExit):
        main(["--seeds", "2", "--record", "x.jsonl"])


def test_zero_trade_run_explains_itself(capsys):
    main(["--days", "0.1", "--warmup-days", "0"])  # midnight to 02:24: entirely off-hours
    assert "No trades: every evaluation fell outside an enabled regime" in capsys.readouterr().out
