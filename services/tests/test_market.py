import numpy as np
import pandas as pd
import pytest

from common import market


@pytest.fixture
def df():
    """200-row synthetic OHLC frame: a mild uptrend with intrabar range."""
    n = 200
    close = pd.Series(100 + np.arange(n) * 0.1)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": pd.Series(np.ones(n)),
    })


def test_atr_positive_and_scales_with_range(df):
    base = market.atr_pct(df)
    assert base > 0

    wide = df.copy()
    wide["high"] = wide["close"] + 5.0
    wide["low"] = wide["close"] - 5.0
    assert market.atr_pct(wide) > base


def test_pct_change_matches_hand_computed():
    closes = pd.Series([100.0, 110.0, 121.0])
    assert market.pct_change(closes, 2) == 21.0
    assert market.pct_change(closes, 1) == 10.0


def test_pct_change_none_on_short_series():
    closes = pd.Series([100.0, 110.0])
    assert market.pct_change(closes, 2) is None
    assert market.pct_change(closes, 5) is None


def test_trajectory_shape_and_last_is_zero(df):
    traj = market.trajectory_pct(df["close"])
    assert len(traj) == 12
    assert traj[-1] == pytest.approx(0.0)
    assert traj[0] < traj[-1]  # oldest first, uptrend => oldest is lower


def test_trajectory_truncates_on_short_series():
    closes = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0])
    traj = market.trajectory_pct(closes)
    assert len(traj) == 3  # only offsets 0, 2, 4 fit in 5 candles
    assert traj[-1] == pytest.approx(0.0)


def test_realized_vol_of_constant_series_is_zero():
    closes = pd.Series([50.0] * 200)
    assert market.realized_vol_daily_pct(closes) == 0.0


def test_summarize_pair_full_key_set(monkeypatch, df):
    monkeypatch.setattr(market, "fetch_klines", lambda pair, **kw: df)
    out = market.summarize_pair("BTC/USDT")
    assert set(out) == {
        "pair", "price", "change_1h_pct", "change_4h_pct", "change_24h_pct",
        "change_7d_pct", "rsi_14", "ema_trend", "price_vs_ema21_pct",
        "atr_14_pct", "vol_daily_pct", "range_24h_pct", "closes_2h_pct",
    }
    assert set(out["range_24h_pct"]) == {"high", "low"}
    assert len(out["closes_2h_pct"]) == 12
    assert out["change_7d_pct"] is not None
