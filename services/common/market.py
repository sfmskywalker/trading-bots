"""Public market data (Binance spot REST, no API key) and basic indicators."""
import logging

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BINANCE_API = "https://api.binance.com/api/v3"
BINANCE_FAPI = "https://fapi.binance.com"
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


def pct_change(closes: pd.Series, bars: int) -> float | None:
    """% change of the last close vs `bars` candles ago; None if too short."""
    if len(closes) <= bars:
        return None
    past = float(closes.iloc[-1 - bars])
    return round((float(closes.iloc[-1]) / past - 1) * 100, 2)


def atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """Wilder ATR as a % of the last close (same EWM smoothing as rsi())."""
    high, low, prev_close = df["high"], df["low"], df["close"].shift()
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    return round(float(atr) / float(df["close"].iloc[-1]) * 100, 2)


def realized_vol_daily_pct(closes: pd.Series, window: int = 168) -> float:
    """Std of hourly returns over `window` candles, scaled to a daily %."""
    returns = closes.pct_change().iloc[-window:]
    return round(float(returns.std()) * (24 ** 0.5) * 100, 2)


def trajectory_pct(closes: pd.Series, points: int = 12, step: int = 2) -> list[float]:
    """Closes sampled every `step` candles as % vs current price, oldest first."""
    price = float(closes.iloc[-1])
    idx = [-1 - i * step for i in range(points) if 1 + i * step <= len(closes)]
    return [round((float(closes.iloc[i]) / price - 1) * 100, 2) for i in reversed(idx)]


def _fapi(path: str, **params) -> list | dict:
    resp = requests.get(f"{BINANCE_FAPI}{path}", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def oi_change_pct(hist: list[dict]) -> float | None:
    """% change of sumOpenInterest, first vs last row; None on empty/short data."""
    if len(hist) < 2:
        return None
    first = float(hist[0]["sumOpenInterest"])
    last = float(hist[-1]["sumOpenInterest"])
    return round((last / first - 1) * 100, 2)


def derivatives(pair: str) -> dict | None:
    """Binance USDT-M futures positioning, or None if the pair has no liquid
    futures market or any endpoint fails (many watchlist pairs are spot-only)."""
    symbol = _symbol(pair)
    try:
        funding = float(_fapi("/fapi/v1/premiumIndex", symbol=symbol)["lastFundingRate"])
        oi_hist = _fapi("/futures/data/openInterestHist", symbol=symbol, period="1h", limit=25)
        lsr = _fapi("/futures/data/globalLongShortAccountRatio", symbol=symbol, period="1h", limit=1)
        return {
            "funding_rate_pct": round(funding * 100, 4),
            "oi_change_24h_pct": oi_change_pct(oi_hist),
            "long_short_ratio": round(float(lsr[-1]["longShortRatio"]), 2),
        }
    except Exception as exc:
        logger.debug("Derivatives fetch failed for %s: %s", pair, exc)
        return None


def summarize_pair(pair: str) -> dict:
    """Compact indicator snapshot suitable for an LLM prompt."""
    df = fetch_klines(pair)
    closes = df["close"]
    price = float(closes.iloc[-1])
    ema21 = float(closes.ewm(span=21, adjust=False).mean().iloc[-1])
    ema55 = float(closes.ewm(span=55, adjust=False).mean().iloc[-1])
    high_24h = float(df["high"].iloc[-24:].max())
    low_24h = float(df["low"].iloc[-24:].min())
    return {
        "pair": pair,
        "price": price,
        "change_1h_pct": pct_change(closes, 1),
        "change_4h_pct": pct_change(closes, 4),
        "change_24h_pct": pct_change(closes, 24),
        "change_7d_pct": pct_change(closes, 168),
        "rsi_14": round(rsi(closes), 1),
        "ema_trend": "up" if ema21 > ema55 else "down",
        "price_vs_ema21_pct": round((price / ema21 - 1) * 100, 2),
        "atr_14_pct": atr_pct(df),
        "vol_daily_pct": realized_vol_daily_pct(closes),
        "range_24h_pct": {
            "high": round((high_24h / price - 1) * 100, 2),
            "low": round((low_24h / price - 1) * 100, 2),
        },
        "closes_2h_pct": trajectory_pct(closes),
        "derivatives": derivatives(pair),
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
