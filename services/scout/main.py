"""Scout: exchange-wide asset discovery for Bot B and the advisor.

Every few hours it scans all Binance USDT pairs, applies the hard filters in
filters.py, enriches the survivors with a 7-day view, and asks Claude to rank
a small watchlist with an investment thesis per pair. The output feeds Bot B's
tradable universe (on top of the core pairs) and the advisor's context.

Without an API key it writes an empty watchlist so the rest of the stack
simply falls back to the core pairs.
"""
import json
import logging
import os
import time

from common import claude, context, market
from common.util import append_jsonl, atomic_write_json, shared_dir, utc_now_iso
from scout import filters

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("scout")

WATCHLIST_SCHEMA = {
    "type": "object",
    "properties": {
        "market_note": {"type": "string"},
        "watchlist": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pair": {"type": "string"},
                    "thesis": {"type": "string"},
                    "conviction": {"type": "number"},
                },
                "required": ["pair", "thesis", "conviction"],
                "additionalProperties": False,
            },
        },
        "avoid": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pair": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["pair", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["market_note", "watchlist", "avoid"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are an asset scout for a crypto paper-trading experiment. You receive a
pre-filtered list of liquid Binance USDT spot pairs (leveraged tokens, stables
and illiquid pairs are already removed by code) with momentum and volume data.

Select AT MOST 6 pairs worth watching for a swing trade on the long side over
the next days, each with a one-or-two sentence thesis grounded in the data you
were given. Also list pairs from the candidates that look like pump-and-dumps
or exhaustion moves under "avoid".

Be selective: an empty or short watchlist is a perfectly good answer. Momentum
chasing after a vertical move is the classic retail mistake — prefer early or
steady strength over spikes. Only include pairs from the candidate list.

The trading bot's currently held pairs are removed from the candidates — pick
complements to that exposure, not duplicates of what it already holds.
"""


def enrich(candidate: dict) -> dict:
    """Add a 7d/RSI view; skip enrichment errors silently (candidate stays basic)."""
    try:
        return {**candidate, **{k: market.summarize_pair(candidate["pair"])[k]
                                for k in ("change_7d_pct", "rsi_14", "ema_trend", "atr_14_pct")}}
    except Exception:
        return candidate


def empty_watchlist(reason: str) -> dict:
    return {"generated_at": utc_now_iso(), "source": "fallback",
            "market_note": reason, "watchlist": [], "avoid": []}


def run_once() -> dict:
    if not claude.has_api_key():
        result = empty_watchlist("ANTHROPIC_API_KEY not configured")
        logger.warning(result["market_note"])
    else:
        try:
            tickers = market.fetch_tickers_24h()
            held = {p["pair"] for p in context.read_bot_positions()}
            cands = [enrich(c) for c in filters.candidates(
                tickers, exclude=set(market.PAIRS) | held)]
            lessons = context.read_lessons()
            user_content = (
                f"Candidates (pre-filtered, {len(cands)} pairs):\n"
                + json.dumps(cands, indent=2)
                + f"\n\nBot holdings excluded from candidates: {sorted(held)}"
                + "\n\nFear & Greed last 7 days:\n"
                + json.dumps(market.fetch_fear_greed())
                + (f"\n\nLessons from past performance reviews:\n{lessons}" if lessons else "")
            )
            picked, usage = claude.call_structured(
                system=SYSTEM_PROMPT, user_content=user_content, schema=WATCHLIST_SCHEMA)
            candidate_pairs = {c["pair"] for c in cands}
            picked["watchlist"] = [w for w in picked["watchlist"]
                                   if w["pair"] in candidate_pairs][:6]
            result = {"generated_at": utc_now_iso(), "source": "claude", **picked}
            append_jsonl(shared_dir() / "scout_log.jsonl", {
                "at": result["generated_at"], "candidates": len(cands),
                "watchlist": picked["watchlist"], "avoid": picked["avoid"],
                "usage": usage,
            })
        except Exception as exc:
            result = empty_watchlist(f"{type(exc).__name__}: {exc}")
            logger.exception("Scout run failed, wrote empty watchlist")

    atomic_write_json(shared_dir() / context.WATCHLIST_FILE, result)
    logger.info("Watchlist written: %s", [w["pair"] for w in result["watchlist"]] or "(empty)")
    return result


def main() -> None:
    interval_hours = float(os.environ.get("SCOUT_INTERVAL_HOURS", "4"))
    while True:
        run_once()
        time.sleep(interval_hours * 3600)


if __name__ == "__main__":
    main()
