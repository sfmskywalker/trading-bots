"""Shared prompt context: post-mortem lessons and the scout's watchlist."""
import json
import logging
from datetime import datetime, timedelta, timezone

from common.util import shared_dir

logger = logging.getLogger(__name__)

LESSONS_FILE = "lessons.md"
WATCHLIST_FILE = "watchlist.json"
WATCHLIST_MAX_AGE_HOURS = 24
LESSONS_MAX_CHARS = 3000


def read_lessons() -> str:
    """Latest post-mortem lessons, empty string if none yet."""
    path = shared_dir() / LESSONS_FILE
    if not path.exists():
        return ""
    return path.read_text()[:LESSONS_MAX_CHARS]


def read_watchlist() -> list[dict]:
    """Scout watchlist entries; empty when missing, stale, or unreadable."""
    path = shared_dir() / WATCHLIST_FILE
    try:
        data = json.loads(path.read_text())
        generated = datetime.fromisoformat(data["generated_at"])
        if datetime.now(timezone.utc) - generated > timedelta(hours=WATCHLIST_MAX_AGE_HOURS):
            logger.warning("Watchlist is stale, ignoring")
            return []
        return data.get("watchlist", [])
    except (OSError, KeyError, ValueError):
        return []
