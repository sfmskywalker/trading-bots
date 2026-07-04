"""BaselineTrend gated by the LLM advisor's market posture.

The advisor service periodically writes ``posture.json`` (mounted read-only at
``user_data/posture/``). Entries and position sizing respect it; exits are
never blocked — the posture can only make the bot more cautious, not trap it
in a position. A missing or stale posture degrades to neutral so the bot keeps
working if the advisor is down.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from freqtrade.persistence import Trade

from BaselineTrend import BaselineTrend

logger = logging.getLogger(__name__)

POSTURE_PATH = Path("/freqtrade/user_data/posture/posture.json")
POSTURE_MAX_AGE_HOURS = 8

NEUTRAL_POSTURE = {
    "regime": "neutral",
    "confidence": 0.0,
    "max_open_trades": 3,
    "stake_multiplier": 1.0,
    "veto_pairs": [],
    "generated_at": None,
}


class AdvisorGatedTrend(BaselineTrend):

    _posture_cache: Optional[dict] = None
    _posture_mtime: float = 0.0

    def _posture(self) -> dict:
        try:
            mtime = POSTURE_PATH.stat().st_mtime
            if self._posture_cache is None or mtime != self._posture_mtime:
                raw = json.loads(POSTURE_PATH.read_text())
                self._posture_cache = {**NEUTRAL_POSTURE, **raw}
                self._posture_mtime = mtime
                logger.info("Loaded advisor posture: %s", self._posture_cache)
        except (OSError, json.JSONDecodeError) as exc:
            if self._posture_cache is None:
                logger.warning("No advisor posture available (%s), using neutral", exc)
            return dict(NEUTRAL_POSTURE)

        posture = self._posture_cache
        generated_at = posture.get("generated_at")
        if generated_at:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(generated_at)
            if age.total_seconds() > POSTURE_MAX_AGE_HOURS * 3600:
                logger.warning("Advisor posture is stale (%.1fh old), using neutral",
                               age.total_seconds() / 3600)
                return dict(NEUTRAL_POSTURE)
        return posture

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time: datetime,
                            entry_tag: Optional[str], side: str, **kwargs) -> bool:
        posture = self._posture()
        if posture["regime"] == "risk_off":
            logger.info("Entry for %s vetoed: advisor regime is risk_off", pair)
            return False
        if pair in posture["veto_pairs"]:
            logger.info("Entry for %s vetoed: pair on advisor veto list", pair)
            return False
        if Trade.get_open_trade_count() >= posture["max_open_trades"]:
            logger.info("Entry for %s vetoed: advisor caps open trades at %d",
                        pair, posture["max_open_trades"])
            return False
        return True

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float],
                            max_stake: float, leverage: float, entry_tag: Optional[str],
                            side: str, **kwargs) -> float:
        # Clamp so a bad posture can never oversize; sizing errors fall back
        # to the proposed stake rather than blocking the trade.
        multiplier = min(max(float(self._posture()["stake_multiplier"]), 0.25), 1.5)
        return max(min(proposed_stake * multiplier, max_stake), min_stake or 0)
