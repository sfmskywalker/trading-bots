"""Weekly post-mortem: the system's learning loop.

Reviews the closed trades of all bots (joining Bot B's trades with the
reasoning Claude gave at entry time), asks Claude what actually worked and
what didn't, and writes shared/lessons.md — which the advisor, trader, and
scout inject into their prompts. Skips the review while the sample is too
small to learn anything but noise.
"""
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import claude, context
from common.util import append_jsonl, read_jsonl, shared_dir, utc_now_iso

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("postmortem")

MIN_CLOSED_TRADES = int(os.environ.get("POSTMORTEM_MIN_TRADES", "10"))
INTERVAL_DAYS = float(os.environ.get("POSTMORTEM_INTERVAL_DAYS", "7"))

BOTS = {
    "quant-bot (advisor-gated trend)": "QUANT_DB",
    "llm-bot (Claude decides trades)": "LLM_DB",
    "freqai-bot (adaptive ML)": "FREQAI_DB",
}

LESSONS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "advisor_lessons": {"type": "string"},
        "trader_lessons": {"type": "string"},
        "scout_lessons": {"type": "string"},
    },
    "required": ["summary", "advisor_lessons", "trader_lessons", "scout_lessons"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are reviewing the performance of a crypto paper-trading experiment with
three bots. You receive their closed trades from the last 90 days; the
LLM-driven bot's trades include the reasoning given at entry time.

Write concrete, evidence-grounded lessons for three consumers:
- advisor_lessons: for the risk advisor that sets market posture (regime,
  sizing, vetoes). What postures helped or hurt?
- trader_lessons: for the LLM trader. Which entry patterns/reasonings made
  money, which lost? Reference actual patterns from the data.
- scout_lessons: for the asset scout picking watchlist candidates. Did
  watchlist picks perform?

Rules: only conclude what the data supports. State sample sizes. If the data
is too thin for a pattern, say "insufficient evidence" for that section rather
than inventing lessons. Max ~150 words per section.
"""


def closed_trades(db_env: str) -> list[dict]:
    db_path = os.environ.get(db_env, "")
    if not db_path or not Path(db_path).exists():
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(" ")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT pair, open_date, close_date, stake_amount, close_profit_abs, "
            "close_profit, exit_reason, enter_tag FROM trades "
            "WHERE is_open = 0 AND close_date > ? ORDER BY close_date", (since,)
        ).fetchall()
    return [dict(r) for r in rows]


def attach_reasoning(trades: list[dict]) -> None:
    """Join Bot B trades with the decision-log reasoning closest to entry."""
    decisions = read_jsonl(shared_dir() / "llm_trader_decisions.jsonl")
    buys = [
        {"at": rec["at"], **ex}
        for rec in decisions for ex in rec.get("executed", [])
        if ex.get("action") == "buy"
    ]
    def ts(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    for trade in trades:
        if not trade.get("open_date"):
            continue
        opened = ts(trade["open_date"])
        matches = [b for b in buys
                   if b.get("pair") == trade["pair"] and ts(b["at"]) <= opened]
        if matches:
            trade["entry_reasoning"] = matches[-1].get("reasoning")


def run_review() -> None:
    data = {}
    total = 0
    for label, env in BOTS.items():
        trades = closed_trades(env)
        if "llm-bot" in label:
            attach_reasoning(trades)
        data[label] = trades
        total += len(trades)

    if total < MIN_CLOSED_TRADES:
        logger.info("Only %d closed trades (< %d) — skipping review, not enough signal",
                    total, MIN_CLOSED_TRADES)
        return

    lessons, usage = claude.call_structured(
        system=SYSTEM_PROMPT,
        user_content=json.dumps(data, indent=2, default=str),
        schema=LESSONS_SCHEMA,
        max_tokens=4096,
    )
    now = utc_now_iso()
    (shared_dir() / context.LESSONS_FILE).write_text(f"""\
# Lessons (post-mortem of {total} closed trades, generated {now})

## Summary
{lessons['summary']}

## For the advisor
{lessons['advisor_lessons']}

## For the trader
{lessons['trader_lessons']}

## For the scout
{lessons['scout_lessons']}
""")
    append_jsonl(shared_dir() / "postmortem_log.jsonl",
                 {"at": now, "trades_reviewed": total, "lessons": lessons, "usage": usage})
    logger.info("lessons.md updated from %d closed trades", total)


def lessons_age_days() -> float:
    path = shared_dir() / context.LESSONS_FILE
    if not path.exists():
        return float("inf")
    return (time.time() - path.stat().st_mtime) / 86400


def main() -> None:
    while True:
        if not claude.has_api_key():
            logger.warning("ANTHROPIC_API_KEY not configured — post-mortem idle")
        elif lessons_age_days() >= INTERVAL_DAYS:
            try:
                run_review()
            except Exception:
                logger.exception("Post-mortem failed; will retry")
        time.sleep(6 * 3600)


if __name__ == "__main__":
    main()
