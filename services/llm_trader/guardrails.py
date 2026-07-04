"""Hard, code-level limits on what the LLM trader may do.

These are deliberately NOT part of the prompt: no matter what Claude decides,
an action that violates a limit here is rejected before it reaches the bot.
"""
import os
from dataclasses import dataclass, field

from common.util import shared_dir


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


MAX_STAKE_USDT = _env_float("GUARD_MAX_STAKE_USDT", 1500)
MAX_OPEN_TRADES = int(_env_float("GUARD_MAX_OPEN_TRADES", 3))
MAX_TRADES_PER_DAY = int(_env_float("GUARD_MAX_TRADES_PER_DAY", 6))
MAX_DAILY_LOSS_PCT = _env_float("GUARD_MAX_DAILY_LOSS_PCT", 3.0)
KILL_SWITCH_FILE = "KILL"


@dataclass
class BotState:
    """What the guardrails need to know about the bot right now."""
    open_trade_count: int
    open_trade_ids: list[int] = field(default_factory=list)
    entries_today: int = 0
    daily_loss_pct: float = 0.0  # positive number = loss


def kill_switch_active() -> bool:
    return (shared_dir() / KILL_SWITCH_FILE).exists()


def validate(decision: dict, state: BotState, allowed_pairs: list[str]) -> tuple[bool, str]:
    """Return (allowed, reason). Reason explains a rejection.

    allowed_pairs is the cycle's tradable universe: the core pairs plus
    whatever the scout's (code-filtered) watchlist currently contains.
    """
    if kill_switch_active():
        return False, f"kill switch file present ({KILL_SWITCH_FILE})"

    action = decision.get("action")
    if action == "hold":
        return True, "hold is always allowed"

    if action == "buy":
        if decision.get("pair") not in allowed_pairs:
            return False, f"pair {decision.get('pair')} not in allowed universe"
        stake = decision.get("stake_usdt") or 0
        if not 0 < stake <= MAX_STAKE_USDT:
            return False, f"stake {stake} outside (0, {MAX_STAKE_USDT}]"
        if state.open_trade_count >= MAX_OPEN_TRADES:
            return False, f"already {state.open_trade_count} open trades (max {MAX_OPEN_TRADES})"
        if state.entries_today >= MAX_TRADES_PER_DAY:
            return False, f"already {state.entries_today} entries today (max {MAX_TRADES_PER_DAY})"
        if state.daily_loss_pct >= MAX_DAILY_LOSS_PCT:
            return False, f"daily loss {state.daily_loss_pct:.1f}% >= {MAX_DAILY_LOSS_PCT}% cap"
        return True, "buy within limits"

    if action == "sell":
        trade_id = decision.get("trade_id")
        if trade_id not in state.open_trade_ids:
            return False, f"trade_id {trade_id} is not an open trade"
        return True, "sell of open trade is allowed"

    return False, f"unknown action {action!r}"
