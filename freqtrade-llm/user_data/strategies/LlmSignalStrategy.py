"""Shell strategy for Bot B — the LLM-decides-everything experiment.

This strategy never generates entries itself: all entries and most exits come
from the llm-trader service through the REST API (force entry / force exit).
What lives here are the non-negotiable safety nets that apply no matter what
the LLM says: a hard stoploss and a maximum holding time.
"""
from datetime import datetime
from typing import Optional

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy
from pandas import DataFrame

MAX_HOLD_DAYS = 7


class LlmSignalStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False
    startup_candle_count = 0
    process_only_new_candles = True

    # The LLM manages exits; ROI table is disabled but the stoploss is a hard
    # floor the LLM cannot override.
    minimal_roi = {"0": 100}
    stoploss = -0.05
    use_exit_signal = False

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs) -> Optional[str]:
        if (current_time - trade.open_date_utc).days >= MAX_HOLD_DAYS:
            return "max_hold_time"
        return None
