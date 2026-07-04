"""Pure signal + accounting logic for the delta-neutral funding-carry bot.

NO I/O, NO network, NO wall-clock reads here — every timestamp arrives as an
argument so the same functions drive both the live loop and the backtest.

A position dict looks like::

    position = {
        "pair": "BTC/USDT", "notional_usdt": 2000.0, "opened_at": iso,
        "spot_entry": float, "perp_entry": float,
        "qty_spot": notional / spot_entry, "qty_perp": notional / perp_entry,
        "entry_fees_usdt": float, "funding_usdt": 0.0,
        "last_funding_time": int_ms, "bad_periods": 0,
    }

A ledger event: {"at": iso, "type": "open|funding|close", "pair", "pnl_usdt",
...}. The cumulative sum of pnl_usdt over all events == balance - start balance.
"""
import logging
import statistics

logger = logging.getLogger(__name__)

MS_PER_DAY = 86_400_000


def annualized_pct(rate: float, periods_per_day: float = 3.0) -> float:
    """Per-period funding rate -> simple annualized %, e.g. 0.0001 -> 10.95."""
    return rate * periods_per_day * 365 * 100


def periods_per_day(events: list[dict]) -> float:
    """Infer settlement cadence (3/day for 8h, 6/day for 4h) from median spacing
    of funding_time; 3.0 fallback on <2 events."""
    if len(events) < 2:
        return 3.0
    times = sorted(e["funding_time"] for e in events)
    spacings = [b - a for a, b in zip(times, times[1:]) if b > a]
    if not spacings:
        return 3.0
    median_ms = statistics.median(spacings)
    return MS_PER_DAY / median_ms


def entry_signal(events: list[dict], current_rate: float,
                 entry_apr_pct: float, lookback_days: float) -> bool:
    """True when current_rate annualized >= entry_apr_pct AND the mean settled
    rate over the last lookback_days is > 0. events oldest-first; too little
    history (no events inside the window) is conservatively False."""
    if not events:
        return False
    ppd = periods_per_day(events)
    if annualized_pct(current_rate, ppd) < entry_apr_pct:
        return False
    cutoff = max(e["funding_time"] for e in events) - lookback_days * MS_PER_DAY
    window = [e["funding_rate"] for e in events if e["funding_time"] >= cutoff]
    if not window:
        return False
    return statistics.mean(window) > 0


def exit_signal(bad_periods: int, exit_bad_periods: int) -> bool:
    return bad_periods >= exit_bad_periods


def count_bad_periods(prev_bad: int, new_events: list[dict],
                      exit_apr_pct: float) -> int:
    """Fold newly settled events into the consecutive-below-threshold counter
    (reset to 0 on any good period). new_events oldest-first."""
    ppd = periods_per_day(new_events) if len(new_events) >= 2 else 3.0
    bad = prev_bad
    for e in new_events:
        if annualized_pct(e["funding_rate"], ppd) <= exit_apr_pct:
            bad += 1
        else:
            bad = 0
    return bad


def open_position(pair: str, notional: float, spot_price: float,
                  perp_price: float, spot_fee_pct: float, perp_fee_pct: float,
                  now_iso: str, last_funding_time: int = 0) -> tuple[dict, dict]:
    """-> (position, ledger_event). Entry fees = notional*(spot+perp fee)/100,
    charged once here. Event pnl_usdt == -fees."""
    fees = notional * (spot_fee_pct + perp_fee_pct) / 100
    position = {
        "pair": pair,
        "notional_usdt": notional,
        "opened_at": now_iso,
        "spot_entry": spot_price,
        "perp_entry": perp_price,
        "qty_spot": notional / spot_price,
        "qty_perp": notional / perp_price,
        "entry_fees_usdt": fees,
        "funding_usdt": 0.0,
        "last_funding_time": last_funding_time,
        "bad_periods": 0,
    }
    event = {
        "at": now_iso, "type": "open", "pair": pair,
        "notional_usdt": notional, "pnl_usdt": -fees,
    }
    return position, event


def apply_settlements(position: dict, events: list[dict]) -> tuple[dict, list[dict]]:
    """Credit notional * funding_rate for each event strictly newer than
    last_funding_time (short perp RECEIVES positive funding). Idempotent on
    re-poll. Updates funding_usdt and last_funding_time; the caller folds the
    same events into bad_periods via count_bad_periods.
    -> (updated position, ledger events of type "funding")."""
    pos = dict(position)
    fresh = sorted(
        (e for e in events if e["funding_time"] > pos["last_funding_time"]),
        key=lambda e: e["funding_time"],
    )
    ledger = []
    for e in fresh:
        pnl = pos["notional_usdt"] * e["funding_rate"]
        pos["funding_usdt"] += pnl
        pos["last_funding_time"] = e["funding_time"]
        ledger.append({
            "at": e["funding_time"], "type": "funding", "pair": pos["pair"],
            "funding_rate": e["funding_rate"], "pnl_usdt": pnl,
        })
    return pos, ledger


def close_position(position: dict, spot_price: float, perp_price: float,
                   spot_fee_pct: float, perp_fee_pct: float, now_iso: str,
                   reason: str) -> tuple[dict, dict]:
    """-> (closed_record, ledger_event type "close").

    basis_pnl = qty_spot*(spot_exit - spot_entry) + qty_perp*(perp_entry - perp_exit)
    exit fees on exit-side notionals; event pnl_usdt = basis_pnl - exit_fees.
    Funding was already booked per settlement, so it is NOT included here."""
    basis_pnl = (
        position["qty_spot"] * (spot_price - position["spot_entry"])
        + position["qty_perp"] * (position["perp_entry"] - perp_price)
    )
    exit_fees = (
        position["qty_spot"] * spot_price * spot_fee_pct / 100
        + position["qty_perp"] * perp_price * perp_fee_pct / 100
    )
    pnl = basis_pnl - exit_fees
    record = {
        **position,
        "closed_at": now_iso, "spot_exit": spot_price, "perp_exit": perp_price,
        "reason": reason, "basis_pnl_usdt": basis_pnl, "exit_fees_usdt": exit_fees,
    }
    event = {
        "at": now_iso, "type": "close", "pair": position["pair"],
        "reason": reason, "basis_pnl_usdt": basis_pnl,
        "exit_fees_usdt": exit_fees, "pnl_usdt": pnl,
    }
    return record, event


def mark_to_market(position: dict, spot_price: float, perp_price: float) -> dict:
    """Unrealized basis PnL + funding collected, for the positions file / logs."""
    basis_pnl = (
        position["qty_spot"] * (spot_price - position["spot_entry"])
        + position["qty_perp"] * (position["perp_entry"] - perp_price)
    )
    return {
        "pair": position["pair"],
        "notional_usdt": position["notional_usdt"],
        "unrealized_basis_usdt": basis_pnl,
        "funding_usdt": position["funding_usdt"],
        "net_usdt": basis_pnl + position["funding_usdt"] - position["entry_fees_usdt"],
    }


def select_universe(perp_tickers: list[dict], min_quote_volume: float) -> list[str]:
    """Liquid perp pairs by 24h quote volume, sorted desc."""
    liquid = [t for t in perp_tickers if t["quote_volume_24h"] >= min_quote_volume]
    liquid.sort(key=lambda t: t["quote_volume_24h"], reverse=True)
    return [t["pair"] for t in liquid]
