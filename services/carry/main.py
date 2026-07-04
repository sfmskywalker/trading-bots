"""Carry (Bot D): mechanical delta-neutral funding-rate carry, no LLM, no keys.

Each cycle: settle + maybe exit open positions, then consider a few new entries
from the liquid perp universe when funding is rich. Everything is driven by
settled funding history since each position's last_funding_time, so waking
hourly — or after days of downtime — is self-healing (no funding is missed or
double-counted). Paper only.
"""
import logging
import time

from common import market
from common.util import utc_now_iso

from carry import config, engine, state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("carry")

MAX_NEW_ENTRIES_PER_CYCLE = 2


def _settle_and_exit(state_dict: dict, premium: dict) -> list[dict]:
    """Fold newly settled funding into each open position and close any that
    have gone cold. Each pair is isolated so one flaky symbol can't kill the
    cycle. Mutates state_dict in place; returns ledger events to log."""
    ledger: list[dict] = []
    for pair, pos in list(state_dict["positions"].items()):
        try:
            events = market.fetch_funding_history(
                pair, start_time_ms=pos["last_funding_time"] + 1)
            pos, fund_evs = engine.apply_settlements(pos, events)
            for e in fund_evs:
                state_dict["balance_usdt"] += e["pnl_usdt"]
            pos["bad_periods"] = engine.count_bad_periods(
                pos["bad_periods"], events, config.EXIT_APR_PCT)
            state_dict["positions"][pair] = pos
            ledger.extend(fund_evs)

            if engine.exit_signal(pos["bad_periods"], config.EXIT_BAD_PERIODS):
                perp = premium.get(pair, {}).get("mark_price") or market.fetch_spot_price(pair)
                spot = market.fetch_spot_price(pair)
                _, close_ev = engine.close_position(
                    pos, spot, perp, config.SPOT_FEE_PCT, config.PERP_FEE_PCT,
                    utc_now_iso(), reason="funding_cold")
                state_dict["balance_usdt"] += close_ev["pnl_usdt"]
                state_dict["closed_count"] += 1
                del state_dict["positions"][pair]
                ledger.append(close_ev)
                logger.info("Closed %s (%s) net=%.2f", pair,
                            close_ev["reason"], close_ev["pnl_usdt"])
        except Exception as exc:
            logger.warning("Settle/exit failed for %s: %s", pair, exc)
    return ledger


def _consider_entries(state_dict: dict, premium: dict) -> list[dict]:
    """Scan the liquid perp universe and open up to MAX_NEW_ENTRIES_PER_CYCLE
    positions where funding is rich, a spot leg exists, and caps allow."""
    ledger: list[dict] = []
    if len(state_dict["positions"]) >= config.MAX_CONCURRENT:
        return ledger

    universe = engine.select_universe(
        market.fetch_perp_tickers_24h(), config.MIN_QUOTE_VOLUME_24H)
    lookback = int(config.LOOKBACK_DAYS * 6) + 8  # generous even at 4h cadence
    opened = 0
    for pair in universe:
        if opened >= MAX_NEW_ENTRIES_PER_CYCLE:
            break
        if len(state_dict["positions"]) >= config.MAX_CONCURRENT:
            break
        if pair in state_dict["positions"] or pair not in premium:
            continue
        if state_dict["balance_usdt"] < config.MAX_NOTIONAL_PER_PAIR:
            break
        try:
            hist = market.fetch_funding_history(pair, limit=lookback)
            if not engine.entry_signal(hist, premium[pair]["last_funding_rate"],
                                       config.ENTRY_APR_PCT, config.LOOKBACK_DAYS):
                continue
            spot = market.fetch_spot_price(pair)  # raises -> no spot leg, skip
            perp = premium[pair]["mark_price"]
            last_funding_time = max((e["funding_time"] for e in hist), default=0)
            pos, open_ev = engine.open_position(
                pair, config.MAX_NOTIONAL_PER_PAIR, spot, perp,
                config.SPOT_FEE_PCT, config.PERP_FEE_PCT, utc_now_iso(),
                last_funding_time=last_funding_time)
            state_dict["positions"][pair] = pos
            state_dict["balance_usdt"] += open_ev["pnl_usdt"]
            ledger.append(open_ev)
            opened += 1
            logger.info("Opened %s notional=%.0f fees=%.2f", pair,
                        pos["notional_usdt"], pos["entry_fees_usdt"])
        except Exception as exc:
            logger.warning("Entry check failed for %s: %s", pair, exc)
    return ledger


def run_once() -> dict:
    state_dict = state.load_state()
    premium = market.fetch_premium_index_all()

    ledger = _settle_and_exit(state_dict, premium)
    ledger += _consider_entries(state_dict, premium)

    state.save_state(state_dict)
    state.log_events(ledger)

    kinds = {"open": 0, "funding": 0, "close": 0}
    for e in ledger:
        kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    logger.info(
        "Cycle done: %d open positions, balance=%.2f, events(open=%d funding=%d close=%d)",
        len(state_dict["positions"]), state_dict["balance_usdt"],
        kinds["open"], kinds["funding"], kinds["close"])
    return {"balance_usdt": state_dict["balance_usdt"],
            "open_positions": list(state_dict["positions"]),
            "events": kinds}


def main() -> None:
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Carry cycle failed")
        time.sleep(config.INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
