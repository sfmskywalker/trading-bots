"""Env-var constants for the funding-carry bot (guardrails.py style)."""
import os


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


START_BALANCE_USDT = _env_float("CARRY_START_BALANCE_USDT", 10_000)
MAX_NOTIONAL_PER_PAIR = _env_float("CARRY_MAX_NOTIONAL_USDT", 2_000)
MAX_CONCURRENT = int(_env_float("CARRY_MAX_CONCURRENT", 4))
MIN_QUOTE_VOLUME_24H = _env_float("CARRY_MIN_QUOTE_VOLUME", 100_000_000)  # perp 24h
ENTRY_APR_PCT = _env_float("CARRY_ENTRY_APR_PCT", 15.0)   # current rate annualized
LOOKBACK_DAYS = _env_float("CARRY_LOOKBACK_DAYS", 3.0)    # avg funding must be > 0
EXIT_APR_PCT = _env_float("CARRY_EXIT_APR_PCT", 0.0)      # below this counts as "bad"
EXIT_BAD_PERIODS = int(_env_float("CARRY_EXIT_BAD_PERIODS", 3))  # M consecutive
SPOT_FEE_PCT = _env_float("CARRY_SPOT_FEE_PCT", 0.10)     # per side, taker
PERP_FEE_PCT = _env_float("CARRY_PERP_FEE_PCT", 0.05)     # per side, taker
INTERVAL_MINUTES = _env_float("CARRY_INTERVAL_MINUTES", 60)
POSITIONS_FILE = "carry_positions.json"
LEDGER_FILE = "carry_ledger.jsonl"
