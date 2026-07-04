"""LLM advisor: periodically writes a market posture that gates Bot A.

Every N hours it feeds a compact market snapshot to Claude and stores the
resulting posture in shared/posture.json. On any failure — missing API key,
network error, refusal, invalid output — it writes a neutral posture so the
quant bot keeps trading with default behavior.
"""
import json
import logging
import time

from common import claude, context, market
from common.util import append_jsonl, atomic_write_json, shared_dir, utc_now_iso
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("advisor")

POSTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "regime": {"type": "string", "enum": ["risk_on", "neutral", "risk_off"]},
        "confidence": {"type": "number"},
        "max_open_trades": {"type": "integer", "enum": [0, 1, 2, 3]},
        "stake_multiplier": {"type": "number"},
        "veto_pairs": {
            "type": "array",
            "items": {"type": "string"},
        },
        "rationale": {"type": "string"},
    },
    "required": ["regime", "confidence", "max_open_trades", "stake_multiplier",
                 "veto_pairs", "rationale"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are a risk advisor for a crypto paper-trading experiment. A trend-following
bot trades the top ~30 Binance USDT spot pairs by volume on 1h candles. You do
NOT pick trades. You only set the overall risk posture that gates the bot's
entries. The market snapshot shows the majors (a proxy for overall regime) and
a scout's current watchlist for extra context.

Guidelines:
- regime: risk_off when the market shows broad weakness, panic, or a sharp
  regime change; risk_on only with broad confirmed strength; otherwise neutral.
- stake_multiplier between 0.25 (very defensive) and 1.5 (aggressive), 1.0 default.
- veto_pairs: pairs (as "XXX/USDT") showing idiosyncratic weakness or
  pump-and-dump behavior the bot should not enter.
- Be conservative. The cost of missing a trade is lower than the cost of a
  drawdown. When the data is ambiguous, choose neutral with multiplier 1.0.
- rationale: 2-3 sentences max.
"""


def neutral_posture(reason: str) -> dict:
    return {
        "regime": "neutral",
        "confidence": 0.0,
        "max_open_trades": 3,
        "stake_multiplier": 1.0,
        "veto_pairs": [],
        "rationale": f"Fallback neutral posture: {reason}",
        "generated_at": utc_now_iso(),
        "source": "fallback",
    }


def generate_posture() -> dict:
    snapshot = market.market_snapshot()
    watchlist = context.read_watchlist()
    lessons = context.read_lessons()
    user_content = (
        "Current market snapshot (majors):\n" + json.dumps(snapshot, indent=2)
        + "\n\nScout watchlist:\n" + json.dumps(watchlist, indent=2)
        + (f"\n\nLessons from past performance reviews:\n{lessons}" if lessons else "")
    )
    posture, usage = claude.call_structured(
        system=SYSTEM_PROMPT,
        user_content=user_content,
        schema=POSTURE_SCHEMA,
    )
    posture["generated_at"] = utc_now_iso()
    posture["source"] = "claude"
    append_jsonl(shared_dir() / "advisor_log.jsonl", {
        "at": posture["generated_at"],
        "snapshot": snapshot,
        "posture": posture,
        "usage": usage,
    })
    return posture


def run_once() -> dict:
    if not claude.has_api_key():
        posture = neutral_posture("ANTHROPIC_API_KEY not configured")
        logger.warning(posture["rationale"])
    else:
        try:
            posture = generate_posture()
        except Exception as exc:
            posture = neutral_posture(f"{type(exc).__name__}: {exc}")
            logger.exception("Posture generation failed, wrote neutral posture")
    atomic_write_json(shared_dir() / "posture.json", posture)
    logger.info("Posture written: regime=%s multiplier=%s veto=%s",
                posture["regime"], posture["stake_multiplier"], posture["veto_pairs"])
    return posture


def main() -> None:
    interval_hours = float(os.environ.get("ADVISOR_INTERVAL_HOURS", "4"))
    while True:
        run_once()
        time.sleep(interval_hours * 3600)


if __name__ == "__main__":
    main()
