"""Baseline EMA/RSI trend-following strategy — the control group.

Deliberately simple and well-understood: enter on an EMA golden cross with an
RSI sanity filter, exit on the reverse cross or overbought blow-off. Every
LLM-enhanced variant is measured against this.
"""
import talib.abstract as ta
from freqtrade.strategy import IStrategy
from freqtrade.vendor.qtpylib import indicators as qtpylib
from pandas import DataFrame


class BaselineTrend(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False
    startup_candle_count = 60
    process_only_new_candles = True

    minimal_roi = {
        "0": 0.08,
        "240": 0.04,
        "720": 0.02,
        "1440": 0.005
    }
    stoploss = -0.06

    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.04
    trailing_only_offset_is_reached = True

    ema_fast_period = 21
    ema_slow_period = 55
    rsi_period = 14
    rsi_entry_ceiling = 72
    rsi_exit_overbought = 82

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=self.ema_fast_period)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=self.ema_slow_period)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                qtpylib.crossed_above(dataframe["ema_fast"], dataframe["ema_slow"])
                & (dataframe["close"] > dataframe["ema_fast"])
                & (dataframe["rsi"] < self.rsi_entry_ceiling)
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                qtpylib.crossed_below(dataframe["ema_fast"], dataframe["ema_slow"])
                | (dataframe["rsi"] > self.rsi_exit_overbought)
            ),
            "exit_long",
        ] = 1
        return dataframe
