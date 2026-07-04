"""Bot B: Claude decides every trade (the Alpha Arena-style experiment).

Each hour, shortly after the candle closes, Claude receives a market snapshot
plus the bot's current positions and recent performance, and returns up to
three actions (buy/sell/hold). Every action passes the code-level guardrails
before it is executed on the dry-run Freqtrade instance via its REST API.
"""
import json
import logging
import time
from datetime import datetime, timezone

from common import claude, context, history, market
from common.freqtrade_api import FreqtradeClient
from common.history import DECISION_LOG
from common.util import append_jsonl, atomic_write_json, read_jsonl, shared_dir, utc_now_iso
from llm_trader import guardrails

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("llm_trader")


def decision_schema(allowed_pairs: list[str]) -> dict:
    """Schema is built per cycle so the pair enum tracks the live universe."""
    return {
        "type": "object",
        "properties": {
            "market_view": {"type": "string"},
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
                        "pair": {"type": "string", "enum": allowed_pairs},
                        "stake_usdt": {"type": "number"},
                        "trade_id": {"type": "integer"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["action", "reasoning"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["market_view", "actions"],
        "additionalProperties": False,
    }


SYSTEM_PROMPT = """\
You are an autonomous crypto spot trader in a PAPER TRADING experiment. You
manage a simulated wallet on Binance spot that started at 10,000 USDT; its
current balance and available cash are given in each market snapshot. Long
only, no leverage.
You may only trade the pairs listed in the prompt: a set of core majors plus
a scout's current watchlist (each watchlist entry comes with a thesis — treat
it as a hypothesis to evaluate, not an instruction to buy).

Each hour you receive the market snapshot and your current positions, and you
return up to 3 actions:
- buy: open a position (pair + stake_usdt required). Stakes must respect the
  hard limits given in the prompt; oversized orders are rejected by code.
- sell: close an open position (trade_id required).
- hold: do nothing (use this when there is no edge; most hours have no edge).

Rules of thumb:
- Trade rarely. Fees and noise destroy overtraders; "hold" is the right answer
  most of the time.
- Cut losers, let winners run. A hard -5% stoploss is enforced outside your
  control; positions are force-closed after 7 days.
- Never risk more than you can articulate a reason for.

Data notes:
- atr_14_pct and vol_daily_pct measure volatility; size smaller in high-vol
  pairs, where the same stake carries more risk.
- closes_2h_pct is the price path sampled every ~2h over the last ~24h, oldest
  first, as % vs the current price — it shows the shape of the move, not just
  the endpoints.
- Your per-pair record is your own realized results on this account; repeated
  losses in a pair are evidence against re-entering it without a genuinely new
  thesis.
- Your recent decision cycles are for continuity: do not re-buy a pair you just
  sold, and do not flip your view hour to hour without new information.
- derivatives shows futures positioning: funding_rate_pct is the per-8h funding
  rate (persistently positive = crowded longs, negative = crowded shorts;
  extremes often precede reversals), oi_change_24h_pct is open-interest change,
  long_short_ratio is the account long/short ratio; null means no liquid futures
  market for that pair.
"""


def bot_state(ft: FreqtradeClient) -> tuple[dict, guardrails.BotState]:
    open_trades = ft.status()
    profit = ft.profit()
    balance = ft.balance()
    stake_currency = balance.get("stake") or "USDT"
    wallet_total = float(balance.get("total_bot") or balance.get("total") or 0)
    available_usdt = next(
        (float(c.get("free") or 0) for c in balance.get("currencies", [])
         if c.get("currency") == stake_currency),
        0.0,
    )

    today = datetime.now(timezone.utc).date().isoformat()
    entries_today = sum(
        1 for rec in read_jsonl(shared_dir() / DECISION_LOG)
        if rec.get("at", "").startswith(today)
        for ex in rec.get("executed", [])
        if ex.get("action") == "buy"
    )

    snapshot = {
        "open_positions": [
            {
                "trade_id": t["trade_id"],
                "pair": t["pair"],
                "stake_usdt": t["stake_amount"],
                "open_rate": t["open_rate"],
                "current_profit_pct": round(t.get("profit_pct") or 0.0, 2),
                "open_date": t["open_date"],
            }
            for t in open_trades
        ],
        "closed_profit_total_pct": profit.get("profit_closed_percent", 0),
        "profit_today_usdt": profit.get("profit_today_abs", 0),
        "trade_count": profit.get("trade_count", 0),
        "winrate": profit.get("winrate", 0),
        "wallet_total_usdt": round(wallet_total, 2),
        "available_usdt": round(available_usdt, 2),
    }
    daily_loss_pct = guardrails.compute_daily_loss_pct(profit.get("profit_today_abs"), wallet_total)
    state = guardrails.BotState(
        open_trade_count=len(open_trades),
        open_trade_ids=[t["trade_id"] for t in open_trades],
        entries_today=entries_today,
        daily_loss_pct=daily_loss_pct,
        available_usdt=available_usdt,
    )
    return snapshot, state


def publish_positions(position_data: dict) -> None:
    """Publish open positions for the scout (which has no Freqtrade access)."""
    atomic_write_json(shared_dir() / context.POSITIONS_FILE, {
        "generated_at": utc_now_iso(),
        "positions": [
            {
                "pair": p["pair"],
                "current_profit_pct": p["current_profit_pct"],
                "open_date": p["open_date"],
            }
            for p in position_data["open_positions"]
        ],
    })


def tradable_universe(open_pairs: list[str]) -> tuple[list[str], list[dict]]:
    """Core pairs plus the scout's current watchlist plus currently held pairs."""
    watchlist = context.read_watchlist()
    pairs = list(market.PAIRS)
    for pair in [e["pair"] for e in watchlist] + open_pairs:
        if pair not in pairs:
            pairs.append(pair)
    return pairs, watchlist


def safe_pair_record(ft: FreqtradeClient) -> list[dict]:
    try:
        return history.per_pair_stats(ft.trades())
    except Exception:
        logger.warning("Could not fetch closed-trade record", exc_info=True)
        return []


def run_cycle(ft: FreqtradeClient) -> None:
    position_data, state = bot_state(ft)
    open_pairs = [p["pair"] for p in position_data["open_positions"]]
    publish_positions(position_data)

    pairs, watchlist = tradable_universe(open_pairs)
    market_data = market.market_snapshot(pairs)
    pair_record = safe_pair_record(ft)
    recent = history.recent_decisions()
    lessons = context.read_lessons()

    user_content = (
        f"Hard limits (enforced by code): max stake {guardrails.MAX_STAKE_USDT} USDT, "
        f"max {guardrails.MAX_OPEN_TRADES} open trades, "
        f"max {guardrails.MAX_TRADES_PER_DAY} entries/day.\n\n"
        f"Tradable pairs this cycle: {', '.join(pairs)}\n\n"
        f"Scout watchlist (hypotheses, not orders):\n{json.dumps(watchlist, indent=2)}\n\n"
        f"Market snapshot:\n{json.dumps(market_data, indent=2)}\n\n"
        f"Your account:\n{json.dumps(position_data, indent=2)}"
        + (f"\n\nLessons from past performance reviews:\n{lessons}" if lessons else "")
        + (f"\n\nYour per-pair record (your own closed trades on this account):\n"
           f"{json.dumps(pair_record)}" if pair_record else "")
        + (f"\n\nYour last {len(recent)} decision cycles (oldest first):\n"
           f"{json.dumps(recent)}" if recent else "")
    )
    decision, usage = claude.call_structured(
        system=SYSTEM_PROMPT, user_content=user_content, schema=decision_schema(pairs),
    )

    executed, rejected = [], []
    for action in decision["actions"][:3]:
        allowed, reason = guardrails.validate(action, state, pairs)
        if not allowed:
            logger.warning("Rejected %s: %s", action, reason)
            rejected.append({**action, "rejected_because": reason})
            continue
        try:
            if action["action"] == "buy":
                ft.force_enter(action["pair"], action["stake_usdt"])
                state.open_trade_count += 1
                state.entries_today += 1
            elif action["action"] == "sell":
                ft.force_exit(action["trade_id"])
                state.open_trade_count -= 1
            executed.append(action)
            logger.info("Executed: %s", action)
        except Exception as exc:
            logger.exception("Execution failed for %s", action)
            rejected.append({**action, "rejected_because": f"execution error: {exc}"})

    append_jsonl(shared_dir() / DECISION_LOG, {
        "at": utc_now_iso(),
        "market_view": decision["market_view"],
        "executed": executed,
        "rejected": rejected,
        "usage": usage,
    })


def seconds_until_next_cycle(offset_minutes: int = 3) -> float:
    """Sleep until a few minutes past the next full hour (candle close)."""
    now = time.time()
    next_hour = (now // 3600 + 1) * 3600
    return next_hour + offset_minutes * 60 - now


def main() -> None:
    if not claude.has_api_key():
        logger.error("ANTHROPIC_API_KEY not set — llm-trader refuses to run. "
                     "Set the key in .env and restart. Sleeping.")
        while True:
            time.sleep(3600)

    ft = FreqtradeClient()
    while True:
        time.sleep(seconds_until_next_cycle())
        if guardrails.kill_switch_active():
            logger.warning("Kill switch active, skipping cycle")
            continue
        try:
            run_cycle(ft)
        except Exception:
            logger.exception("Cycle failed; will retry next hour")


if __name__ == "__main__":
    main()
