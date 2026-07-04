"""Shared prompt context: post-mortem lessons and the scout's watchlist."""
import json
import logging
from datetime import datetime, timedelta, timezone

from common.util import shared_dir

logger = logging.getLogger(__name__)

LESSONS_FILE = "lessons.md"
WATCHLIST_FILE = "watchlist.json"
WATCHLIST_MAX_AGE_HOURS = 24
POSITIONS_FILE = "bot_b_positions.json"
POSITIONS_MAX_AGE_HOURS = 3
LESSONS_MAX_CHARS = 3000


def read_lessons() -> str:
    """Latest post-mortem lessons, empty string if none yet."""
    path = shared_dir() / LESSONS_FILE
    if not path.exists():
        return ""
    return path.read_text()[:LESSONS_MAX_CHARS]


def _read_fresh_json(filename: str, max_age_hours: int) -> dict | None:
    """Parse a shared JSON file; None when missing, stale, or unreadable."""
    path = shared_dir() / filename
    try:
        data = json.loads(path.read_text())
        generated = datetime.fromisoformat(data["generated_at"])
        if datetime.now(timezone.utc) - generated > timedelta(hours=max_age_hours):
            logger.warning("%s is stale, ignoring", filename)
            return None
        return data
    except (OSError, KeyError, ValueError):
        return None


def read_watchlist() -> list[dict]:
    """Scout watchlist entries; empty when missing, stale, or unreadable."""
    data = _read_fresh_json(WATCHLIST_FILE, WATCHLIST_MAX_AGE_HOURS)
    return data.get("watchlist", []) if data else []


def read_bot_positions() -> list[dict]:
    """Bot B's published open positions; empty when missing, stale, or unreadable."""
    data = _read_fresh_json(POSITIONS_FILE, POSITIONS_MAX_AGE_HOURS)
    return data.get("positions", []) if data else []
