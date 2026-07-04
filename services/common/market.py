"""Public market data (Binance spot REST, no API key) and basic indicators."""
import logging

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BINANCE_API = "https://api.binance.com/api/v3"
FEAR_GREED_API = "https://api.alternative.me/fng/?limit=7"

PAIRS = ["ADA/USDT", "BTC/USDT", "ETH/USDT", "SOL/USDT"]


def _symbol(pair: str) -> str:
    return pair.replace("/", "")


def fetch_klines(pair: str, interval: str = "1h", limit: int = 200) -> pd.DataFrame:
    resp = requests.get(
        f"{BINANCE_API}/klines",
        params={"symbol": _symbol(pair), "interval": interval, "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json()
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "_",
    ])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df


def rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss
    return float((100 - 100 / (1 + rs)).iloc[-1])


def summarize_pair(pair: str) -> dict:
    """Compact indicator snapshot suitable for an LLM prompt."""
    df = fetch_klines(pair)
    closes = df["close"]
    price = float(closes.iloc[-1])
    ema21 = float(closes.ewm(span=21, adjust=False).mean().iloc[-1])
    ema55 = float(closes.ewm(span=55, adjust=False).mean().iloc[-1])
    return {
        "pair": pair,
        "price": price,
        "change_24h_pct": round((price / float(closes.iloc[-25]) - 1) * 100, 2),
        "change_7d_pct": round((price / float(closes.iloc[-169]) - 1) * 100, 2),
        "rsi_14": round(rsi(closes), 1),
        "ema_trend": "up" if ema21 > ema55 else "down",
        "price_vs_ema21_pct": round((price / ema21 - 1) * 100, 2),
    }


def fetch_tickers_24h() -> list[dict]:
    """24h stats for every USDT spot pair on Binance."""
    resp = requests.get(f"{BINANCE_API}/ticker/24hr", timeout=20)
    resp.raise_for_status()
    out = []
    for t in resp.json():
        if not t["symbol"].endswith("USDT"):
            continue
        out.append({
            "pair": t["symbol"][:-4] + "/USDT",
            "price": float(t["lastPrice"]),
            "change_24h_pct": float(t["priceChangePercent"]),
            "quote_volume_24h": float(t["quoteVolume"]),
            "trades_24h": int(t["count"]),
        })
    return out


def fetch_fear_greed() -> list[dict]:
    """Crypto Fear & Greed index, last 7 days. Empty list on failure."""
    try:
        resp = requests.get(FEAR_GREED_API, timeout=15)
        resp.raise_for_status()
        return [
            {"value": int(d["value"]), "label": d["value_classification"]}
            for d in resp.json()["data"]
        ]
    except Exception as exc:
        logger.warning("Fear & Greed fetch failed: %s", exc)
        return []


def market_snapshot(pairs: list[str] = PAIRS) -> dict:
    return {
        "pairs": [summarize_pair(p) for p in pairs],
        "fear_greed_last_7d": fetch_fear_greed(),
    }
