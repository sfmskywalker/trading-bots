import json
from datetime import datetime, timedelta, timezone

import pytest

from common import context
from common.util import shared_dir


@pytest.fixture(autouse=True)
def shared_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARED_DIR", str(tmp_path))


def _iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _write(filename, data):
    (shared_dir() / filename).write_text(json.dumps(data))


def test_read_bot_positions_fresh():
    positions = [{"pair": "BTC/USDT", "current_profit_pct": 1.2,
                  "open_date": "2026-07-04T00:00:00+00:00"}]
    _write(context.POSITIONS_FILE, {"generated_at": _iso(1), "positions": positions})
    assert context.read_bot_positions() == positions


def test_read_bot_positions_stale():
    _write(context.POSITIONS_FILE, {
        "generated_at": _iso(context.POSITIONS_MAX_AGE_HOURS + 1),
        "positions": [{"pair": "BTC/USDT"}]})
    assert context.read_bot_positions() == []


def test_read_bot_positions_missing():
    assert context.read_bot_positions() == []


def test_read_bot_positions_malformed():
    (shared_dir() / context.POSITIONS_FILE).write_text("{not json")
    assert context.read_bot_positions() == []


def test_read_watchlist_regression_through_helper():
    watchlist = [{"pair": "DOGE/USDT"}]
    _write(context.WATCHLIST_FILE, {"generated_at": _iso(1), "watchlist": watchlist})
    assert context.read_watchlist() == watchlist

    _write(context.WATCHLIST_FILE, {
        "generated_at": _iso(context.WATCHLIST_MAX_AGE_HOURS + 1),
        "watchlist": watchlist})
    assert context.read_watchlist() == []
