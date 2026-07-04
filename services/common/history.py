"""The system's own history: its past decisions, trade record, and postures."""
import logging

from common.util import shared_dir, tail_jsonl

logger = logging.getLogger(__name__)

DECISION_LOG = "llm_trader_decisions.jsonl"
ADVISOR_LOG = "advisor_log.jsonl"

MARKET_VIEW_MAX_CHARS = 200


def _compact_executed(action: dict) -> dict:
    picked = {k: action.get(k) for k in ("action", "pair", "stake_usdt", "trade_id")}
    return {k: v for k, v in picked.items() if v is not None}


def _compact_rejected(action: dict) -> dict:
    return {
        "action": action.get("action"),
        "pair": action.get("pair"),
        "why": action.get("rejected_because"),
    }


def recent_decisions(n: int = 8) -> list[dict]:
    """Last n llm-trader cycles, compacted for the prompt (oldest first)."""
    out = []
    for rec in tail_jsonl(shared_dir() / DECISION_LOG, n):
        out.append({
            "at": rec.get("at", "")[:16],
            "market_view": (rec.get("market_view") or "")[:MARKET_VIEW_MAX_CHARS],
            "executed": [_compact_executed(a) for a in rec.get("executed", [])],
            "rejected": [_compact_rejected(a) for a in rec.get("rejected", [])],
        })
    return out


def per_pair_stats(closed_trades: list[dict]) -> list[dict]:
    """Group Freqtrade closed trades by pair. Skips trades still open."""
    by_pair: dict[str, list[dict]] = {}
    for t in closed_trades:
        if t.get("close_profit_abs") is None:
            continue
        by_pair.setdefault(t["pair"], []).append(t)

    stats = []
    for pair, trades in sorted(by_pair.items()):
        wins = sum(1 for t in trades if t["close_profit_abs"] > 0)
        stats.append({
            "pair": pair,
            "trades": len(trades),
            "wins": wins,
            "losses": len(trades) - wins,
            "net_profit_usdt": round(sum(t["close_profit_abs"] for t in trades), 2),
            "avg_profit_pct": round(
                sum(t.get("close_profit", 0) or 0 for t in trades) / len(trades) * 100, 2),
            "last_5": "".join(
                "W" if t["close_profit_abs"] > 0 else "L" for t in trades[-5:]),
        })
    return stats


def recent_postures(n: int = 6) -> list[dict]:
    """Last n advisor-log postures, compacted (oldest first)."""
    out = []
    for rec in tail_jsonl(shared_dir() / ADVISOR_LOG, n):
        posture = rec.get("posture", {})
        out.append({
            "at": rec.get("at", "")[:16],
            "regime": posture.get("regime"),
            "stake_multiplier": posture.get("stake_multiplier"),
            "veto_pairs": posture.get("veto_pairs"),
        })
    return out
