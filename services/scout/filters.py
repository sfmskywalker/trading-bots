"""Hard candidate filters for the scout.

Exchange-wide momentum scanning is exactly how bots walk into pump-and-dumps,
so these limits live in code, not in the prompt: Claude only ever ranks pairs
that already passed them.
"""
import os
import re

MIN_QUOTE_VOLUME_24H = float(os.environ.get("SCOUT_MIN_QUOTE_VOLUME", 20_000_000))
MIN_TRADES_24H = 20_000
MAX_CHANGE_24H_PCT = 50.0  # beyond this it's a pump, not a trend

LEVERAGED_RE = re.compile(r"(UP|DOWN|BULL|BEAR)/USDT$")
EXCLUDED_BASES = {
    # stables / fiat / wrapped
    "USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "USD1", "XUSD",
    "AEUR", "EUR", "EURI", "GBP", "TRY", "BRL", "ARS", "COP",
    "WBTC", "WBETH", "WETH",
}


def passes(ticker: dict) -> bool:
    base = ticker["pair"].split("/")[0]
    return (
        ticker["quote_volume_24h"] >= MIN_QUOTE_VOLUME_24H
        and ticker["trades_24h"] >= MIN_TRADES_24H
        and abs(ticker["change_24h_pct"]) <= MAX_CHANGE_24H_PCT
        and base not in EXCLUDED_BASES
        and not LEVERAGED_RE.search(ticker["pair"])
    )


def candidates(tickers: list[dict], exclude: set[str], limit: int = 25) -> list[dict]:
    """Top movers and top volume among filtered tickers, deduped."""
    eligible = [t for t in tickers if passes(t) and t["pair"] not in exclude]
    by_move = sorted(eligible, key=lambda t: abs(t["change_24h_pct"]), reverse=True)
    by_volume = sorted(eligible, key=lambda t: t["quote_volume_24h"], reverse=True)

    seen, merged = set(), []
    for t in by_move[:15] + by_volume[:15]:
        if t["pair"] not in seen:
            seen.add(t["pair"])
            merged.append(t)
    return merged[:limit]
