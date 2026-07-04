"""Backtest the carry signal over full funding history (public API, read-only).

CAVEAT: this measures funding yield minus round-trip fees only. Basis PnL at
entry/exit is assumed 0 — historical spot/perp price pairs at each event are not
reconstructed from funding data alone — so treat the reported APR as an
upper-bound screen for which pairs pay to carry, not a realized return.
"""
import argparse
import time

from common import market

from carry import config, engine

MS_PER_DAY = engine.MS_PER_DAY


def fetch_full_history(pair: str, days: int) -> list[dict]:
    """Paginate /fapi/v1/fundingRate by startTime (1000/page) up to `days` back."""
    start = int(time.time() * 1000) - days * MS_PER_DAY
    out: list[dict] = []
    while True:
        page = market.fetch_funding_history(pair, start_time_ms=start, limit=1000)
        if not page:
            break
        out.extend(page)
        if len(page) < 1000:
            break
        start = page[-1]["funding_time"] + 1
    return out


def simulate_pair(events: list[dict], notional: float, cfg: dict) -> dict:
    """Walk events chronologically, entering on the carry signal and exiting
    after EXIT_BAD_PERIODS cold periods. Basis PnL is 0 (see module caveat);
    fees are charged per round trip via the engine's open/close fee math."""
    events = sorted(events, key=lambda e: e["funding_time"])
    funding_usdt = fees_usdt = 0.0
    round_trips = 0
    pos = None

    for i, e in enumerate(events):
        if pos is None:
            window = events[max(0, i - int(cfg["lookback_days"] * 6) - 8):i + 1]
            if engine.entry_signal(window, e["funding_rate"],
                                   cfg["entry_apr_pct"], cfg["lookback_days"]):
                pos, open_ev = engine.open_position(
                    "sim", notional, 1.0, 1.0, cfg["spot_fee_pct"],
                    cfg["perp_fee_pct"], "sim", last_funding_time=e["funding_time"])
                fees_usdt += -open_ev["pnl_usdt"]
                round_trips += 1
            continue

        pos, fund_evs = engine.apply_settlements(pos, [e])
        for fe in fund_evs:
            funding_usdt += fe["pnl_usdt"]
        pos["bad_periods"] = engine.count_bad_periods(
            pos["bad_periods"], [e], cfg["exit_apr_pct"])
        if engine.exit_signal(pos["bad_periods"], cfg["exit_bad_periods"]):
            _, close_ev = engine.close_position(
                pos, 1.0, 1.0, cfg["spot_fee_pct"], cfg["perp_fee_pct"], "sim", "cold")
            fees_usdt += close_ev["exit_fees_usdt"]
            pos = None

    net = funding_usdt - fees_usdt
    days_in = _days_in_position(events, cfg)
    apr = (net / notional) / days_in * 365 * 100 if days_in > 0 else 0.0
    return {
        "pair": None, "round_trips": round_trips,
        "funding_usdt": round(funding_usdt, 2), "fees_usdt": round(fees_usdt, 2),
        "net_usdt": round(net, 2), "apr_pct": round(apr, 2),
        "days_in_position": round(days_in, 1),
    }


def _days_in_position(events: list[dict], cfg: dict) -> float:
    """Total days held across all round trips, replaying entry/exit timing."""
    events = sorted(events, key=lambda e: e["funding_time"])
    held_ms = 0
    entered_at = None
    bad = 0
    for i, e in enumerate(events):
        if entered_at is None:
            window = events[max(0, i - int(cfg["lookback_days"] * 6) - 8):i + 1]
            if engine.entry_signal(window, e["funding_rate"],
                                   cfg["entry_apr_pct"], cfg["lookback_days"]):
                entered_at = e["funding_time"]
                bad = 0
            continue
        bad = engine.count_bad_periods(bad, [e], cfg["exit_apr_pct"])
        if engine.exit_signal(bad, cfg["exit_bad_periods"]):
            held_ms += e["funding_time"] - entered_at
            entered_at = None
    if entered_at is not None:
        held_ms += events[-1]["funding_time"] - entered_at
    return held_ms / MS_PER_DAY


def _cfg() -> dict:
    return {
        "entry_apr_pct": config.ENTRY_APR_PCT,
        "lookback_days": config.LOOKBACK_DAYS,
        "exit_apr_pct": config.EXIT_APR_PCT,
        "exit_bad_periods": config.EXIT_BAD_PERIODS,
        "spot_fee_pct": config.SPOT_FEE_PCT,
        "perp_fee_pct": config.PERP_FEE_PCT,
    }


def _default_pairs(n: int = 10) -> list[str]:
    tickers = market.fetch_perp_tickers_24h()
    return engine.select_universe(tickers, config.MIN_QUOTE_VOLUME_24H)[:n]


def main() -> None:
    parser = argparse.ArgumentParser(description="Carry funding backtest (screen)")
    parser.add_argument("--pairs", help="comma-separated, e.g. BTC/USDT,ETH/USDT")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--notional", type=float, default=config.MAX_NOTIONAL_PER_PAIR)
    args = parser.parse_args()

    pairs = args.pairs.split(",") if args.pairs else _default_pairs()
    cfg = _cfg()
    results = []
    for pair in pairs:
        try:
            events = fetch_full_history(pair, args.days)
            res = simulate_pair(events, args.notional, cfg)
            res["pair"] = pair
            results.append(res)
        except Exception as exc:
            print(f"{pair}: fetch/sim failed: {exc}")

    results.sort(key=lambda r: r["apr_pct"], reverse=True)
    print(f"\nCarry backtest (screen) — {args.days}d, notional={args.notional:.0f} USDT")
    print("(funding minus fees only; basis PnL excluded — upper-bound estimate)\n")
    header = f"{'pair':<14}{'trips':>6}{'funding':>10}{'fees':>9}{'net':>10}{'days':>8}{'APR%':>9}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r['pair']:<14}{r['round_trips']:>6}{r['funding_usdt']:>10}"
              f"{r['fees_usdt']:>9}{r['net_usdt']:>10}{r['days_in_position']:>8}"
              f"{r['apr_pct']:>9}")


if __name__ == "__main__":
    main()
