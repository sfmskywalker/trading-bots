"""Thin persistence around common.util for the carry bot."""
import json

from common.util import (append_jsonl, atomic_write_json, read_jsonl,
                         shared_dir, utc_now_iso)

from carry import config


def _positions_path():
    return shared_dir() / config.POSITIONS_FILE


def _ledger_path():
    return shared_dir() / config.LEDGER_FILE


def load_state() -> dict:
    """shared/carry_positions.json or a fresh state."""
    path = _positions_path()
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"balance_usdt": config.START_BALANCE_USDT, "positions": {},
            "closed_count": 0}


def save_state(state: dict) -> None:
    """atomic_write_json with a generated_at stamp."""
    atomic_write_json(_positions_path(), {**state, "generated_at": utc_now_iso()})


def log_events(events: list[dict]) -> None:
    """append_jsonl each event to shared/carry_ledger.jsonl."""
    path = _ledger_path()
    for event in events:
        append_jsonl(path, event)


def read_ledger() -> list[dict]:
    return read_jsonl(_ledger_path())
