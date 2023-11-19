import logging
import sys
from enum import Enum
from itertools import combinations
from locale import currency
from typing import Literal

import numpy as np
import pandas as pd

# TODO implement slippage and transaction costs
logging.basicConfig(stream=sys.stdout)
log = logging.getLogger()

spreads_dict = {
    "spread": {
        "AUDUSD": 0.006 / 100,
        "CADUSD": 0.010 / 100,
        "CHFUSD": 0.011 / 100,
        "DKKUSD": 0.005 / 100,
        "EURUSD": 0.0036 / 100,
        "GBPUSD": 0.005 / 100,
        "JPYUSD": 0.006 / 100,
        "NOKUSD": 0.035 / 100,
        "NZDUSD": 0.014 / 100,
        "SEKUSD": 0.032 / 100,
    }
}
spreads = pd.DataFrame.from_dict(spreads_dict, orient="index")


class Backtest:
    def __init__(
        self,
        fx_lon_fixes: pd.DataFrame,
        fx_ny_fixes: pd.DataFrame,
        swaps_lon_fixes: pd.DataFrame,
        swaps_ny_fixes: pd.DataFrame,
        ma_window: int = 15,
        logging_level: int = 0,
    ) -> None:
        self.fx_lon_fixes = fx_lon_fixes
        self.fx_ny_fixes = fx_ny_fixes
        self.swaps_lon_fixes = swaps_lon_fixes
        self.swaps_ny_fixes = swaps_ny_fixes
        log.setLevel(logging_level)

        self.ma_window = ma_window
        self.currencies = fx_lon_fixes.columns
        self.countries = [cur[:3] for cur in self.currencies]
        # initialize positions
        self.positions = pd.DataFrame(
            index=fx_lon_fixes.index, columns=self.currencies, dtype=float
        )
        self.positions.fillna(0, inplace=True)
        # initialize signals for LON and NY fixes
        self.signals_lon = pd.DataFrame(
            index=fx_lon_fixes.index, columns=self.currencies, dtype=int
        )
        self.signals_lon.fillna(0, inplace=True)
        self.signals_ny = self.signals_lon.copy()

    def compute_signals(self, fix: Fixes):
        countries, ma_window = self.countries, self.ma_window

        if fix == Fixes.LON:
            swaps_data = self.swaps_lon_fixes
            signals = self.signals_lon
        else:
            swaps_data = self.swaps_ny_fixes
            signals = self.signals_ny

        pairs = ["".join(pair) for pair in combinations(countries, 2)]
        subsignals = pd.DataFrame(columns=pairs, dtype=float, index=swaps_data.index)
        for i, country1 in enumerate(countries):
            country1_cur = country1 + "USD"
            for country2 in countries[i + 1 :]:
                country2_cur = country2 + "USD"
                # print("-" * 50)
                # print(swaps_data[country1_cur], swaps_data[country2_cur])
                # print("-" * 50)
                diff = swaps_data[country1_cur] - swaps_data[country2_cur]
                avg = diff.rolling(
                    ma_window,
                ).mean()

                subsignals_col = (diff - avg) / np.abs(avg)
                log.debug(f"diff for {country1}-{country2}:\n{diff}")
                log.debug(f"avg for {country1}-{country2}:\n{avg}")
                log.debug(f"subsignals for {country1}-{country2}:\n{subsignals_col}")

                subsignals[country1 + country2] = subsignals_col

        # we now have the subsignals for all dates for all combinations of countries

        # compute the threshold for each country
        subsignals["threshold"] = subsignals.abs().quantile(
            axis=1, q=0.5, numeric_only=True
        )
        log.debug(f"subsignals:\n{subsignals}")

        # compute the composite signals for each country
        log.debug("-" * 20 + "COMPOSITE SIGNALS" + "-" * 20)
        for col in subsignals.drop("threshold", axis=1).columns:
            country1, country2 = col[:3], col[3:]

            thresholds = subsignals["threshold"]
            col_data = subsignals[col].copy()

            # find dates where the subsignal is above the threshold
            # how does this handle NaNs?

            col_data[(col_data.abs() > thresholds) & (col_data >= 0)] = 1
            col_data[(col_data.abs() > thresholds) & (col_data < 0)] = -1
            col_data[col_data.abs() != 1] = 0

            signals[country1 + "USD"] += col_data
            signals[country2 + "USD"] -= col_data

    def compute_positions(self, fix: Fixes, target_gross_exposure: float = 1_000_000):
        log.debug("-" * 20 + "COMPUTE POSITIONS" + "-" * 20)

        signal = self.signals_lon if fix == Fixes.LON else self.signals_ny

        base_amt = target_gross_exposure / signal.abs().sum(axis=1)
        nominal_exposures = signal * base_amt.to_numpy().reshape(-1, 1)
        self.positions[:] = nominal_exposures
        print("POS COLS", self.positions.columns)

    def compute_transaction_costs(self):
        log.debug("-" * 20 + "COMPUTE TC" + "-" * 20)
        positions = self.positions
        spreads = self.spreads.reindex(columns=positions.columns)

        position_chg = positions.diff().abs()
        tc = position_chg * spreads.to_numpy()
        return tc

    def compute_pnl(self):
        log.debug("-" * 20 + "COMPUTE PNL" + "-" * 20)
        positions = self.positions

        returns = self.fx_lon_fixes.pct_change()
        log.debug(f"returns:\n{returns}")
        log.debug(f"positions (shifted):\n{positions.shift(1)}")
        positions, returns = positions.align(returns, join="outer", axis=1)
        print(positions.columns, returns.columns)
        log.debug(f"prod:\n{returns * positions.shift(1)}")
        log.debug(f"prod2:\n{returns * positions}")
        pnl = returns * positions.shift(1)
        pnl["total"] = pnl.sum(axis=1)
        pnl["total_pct"] = pnl["total"] / 1_000_000
        log.debug(f"pnl:\n{pnl}")

        self.pnl = pnl

    def compute_stats(self):
        log.debug("-" * 20 + "COMPUTE STATS" + "-" * 20)

        pnl = self.pnl[["total", "total_pct"]].copy()

        def compute_return(col):
            return col.mean() * len(col)

        def compute_vol(col):
            return col.std() * np.sqrt(len(col))

        y_return = pnl["total_pct"].resample("Y").apply(compute_return)
        y_vol = pnl["total_pct"].resample("Y").apply(compute_vol)
        sharpe = y_return / y_vol

        df = pd.DataFrame({"return": y_return, "vol": y_vol, "sharpe": sharpe})

        print(df)

    def run(self):
        self.compute_signals(Fixes.LON)
        self.compute_positions(Fixes.LON)
        self.compute_pnl()
