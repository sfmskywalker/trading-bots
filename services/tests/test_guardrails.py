import pytest

from llm_trader import guardrails
from llm_trader.guardrails import BotState


@pytest.fixture
def state():
    return BotState(open_trade_count=1, open_trade_ids=[7], entries_today=2,
                    daily_loss_pct=0.5)


@pytest.fixture(autouse=True)
def no_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARED_DIR", str(tmp_path))


PAIRS = ["ADA/USDT", "BTC/USDT", "ETH/USDT", "SOL/USDT"]


def buy(pair="ADA/USDT", stake=500):
    return {"action": "buy", "pair": pair, "stake_usdt": stake, "reasoning": "test"}


def test_hold_always_allowed(state):
    allowed, _ = guardrails.validate({"action": "hold", "reasoning": "no edge"}, state, PAIRS)
    assert allowed


def test_valid_buy_allowed(state):
    allowed, reason = guardrails.validate(buy(), state, PAIRS)
    assert allowed, reason


def test_oversized_stake_rejected(state):
    allowed, reason = guardrails.validate(buy(stake=guardrails.MAX_STAKE_USDT + 1), state, PAIRS)
    assert not allowed and "stake" in reason


def test_zero_stake_rejected(state):
    allowed, _ = guardrails.validate(buy(stake=0), state, PAIRS)
    assert not allowed


def test_pair_outside_universe_rejected(state):
    allowed, reason = guardrails.validate(buy(pair="DOGE/USDT"), state, PAIRS)
    assert not allowed and "universe" in reason


def test_watchlist_pair_in_universe_allowed(state):
    allowed, reason = guardrails.validate(
        buy(pair="DOGE/USDT"), state, PAIRS + ["DOGE/USDT"])
    assert allowed, reason


def test_max_open_trades_rejected(state):
    state.open_trade_count = guardrails.MAX_OPEN_TRADES
    allowed, _ = guardrails.validate(buy(), state, PAIRS)
    assert not allowed


def test_daily_trade_cap_rejected(state):
    state.entries_today = guardrails.MAX_TRADES_PER_DAY
    allowed, _ = guardrails.validate(buy(), state, PAIRS)
    assert not allowed


def test_daily_loss_cap_rejected(state):
    state.daily_loss_pct = guardrails.MAX_DAILY_LOSS_PCT
    allowed, reason = guardrails.validate(buy(), state, PAIRS)
    assert not allowed and "loss" in reason


def test_sell_of_open_trade_allowed(state):
    allowed, _ = guardrails.validate(
        {"action": "sell", "trade_id": 7, "reasoning": "take profit"}, state, PAIRS)
    assert allowed


def test_sell_of_unknown_trade_rejected(state):
    allowed, _ = guardrails.validate(
        {"action": "sell", "trade_id": 99, "reasoning": "oops"}, state, PAIRS)
    assert not allowed


def test_kill_switch_blocks_everything(state, tmp_path):
    (tmp_path / guardrails.KILL_SWITCH_FILE).touch()
    allowed, reason = guardrails.validate(buy(), state, PAIRS)
    assert not allowed and "kill switch" in reason
