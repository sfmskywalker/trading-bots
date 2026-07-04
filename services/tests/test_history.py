import pytest

from common import history
from common.util import append_jsonl, shared_dir


@pytest.fixture(autouse=True)
def shared_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARED_DIR", str(tmp_path))


def _decision(at, view="ok", executed=None, rejected=None):
    return {
        "at": at,
        "market_view": view,
        "executed": executed or [],
        "rejected": rejected or [],
    }


def test_recent_decisions_order_truncation_and_filtering():
    log = shared_dir() / history.DECISION_LOG
    for i in range(10):
        append_jsonl(log, _decision(f"2026-07-04T0{i}:30:00+00:00"))
    # newest cycle carries a long view and full-shaped actions
    append_jsonl(log, _decision(
        "2026-07-04T23:59:00+00:00",
        view="x" * 500,
        executed=[{"action": "buy", "pair": "BTC/USDT", "stake_usdt": 500,
                   "trade_id": None, "reasoning": "drop me"}],
        rejected=[{"action": "buy", "pair": "ETH/USDT",
                   "rejected_because": "over cap", "reasoning": "drop"}],
    ))

    out = history.recent_decisions(n=8)
    assert len(out) == 8
    assert out[-1]["at"] == "2026-07-04T23:59"
    assert out[0]["at"] == "2026-07-04T03:30"  # oldest of the last 8
    assert len(out[-1]["market_view"]) == history.MARKET_VIEW_MAX_CHARS

    ex = out[-1]["executed"][0]
    assert ex == {"action": "buy", "pair": "BTC/USDT", "stake_usdt": 500}
    assert out[-1]["rejected"][0] == {
        "action": "buy", "pair": "ETH/USDT", "why": "over cap"}


def _trade(pair, close_profit_abs, close_profit=None):
    return {"pair": pair, "close_profit_abs": close_profit_abs,
            "close_profit": close_profit}


def test_per_pair_stats_wins_losses_and_open_ignored():
    trades = [
        _trade("BTC/USDT", 10.0, 0.02),
        _trade("BTC/USDT", -5.0, -0.01),
        _trade("BTC/USDT", 3.0, 0.006),
        _trade("ADA/USDT", 1.0, 0.01),
        _trade("BTC/USDT", None),  # still open -> ignored
    ]
    stats = history.per_pair_stats(trades)
    assert [s["pair"] for s in stats] == ["ADA/USDT", "BTC/USDT"]  # sorted

    btc = next(s for s in stats if s["pair"] == "BTC/USDT")
    assert btc["trades"] == 3
    assert btc["wins"] == 2 and btc["losses"] == 1
    assert btc["net_profit_usdt"] == 8.0
    assert btc["last_5"] == "WLW"


def test_recent_postures():
    log = shared_dir() / history.ADVISOR_LOG
    append_jsonl(log, {"at": "2026-07-04T01:00:00+00:00", "posture": {
        "regime": "risk_off", "stake_multiplier": 0.5, "veto_pairs": ["SOL/USDT"]}})
    append_jsonl(log, {"at": "2026-07-04T05:00:00+00:00", "posture": {
        "regime": "neutral", "stake_multiplier": 1.0, "veto_pairs": []}})

    out = history.recent_postures(n=6)
    assert len(out) == 2
    assert out[-1] == {"at": "2026-07-04T05:00", "regime": "neutral",
                       "stake_multiplier": 1.0, "veto_pairs": []}
    assert out[0]["regime"] == "risk_off"
