"""Deterministic, network-free fixture data conforming to data.schema.COMMON_COLUMNS.

Used by viz-ux-designer / streamlit-builder to build UI before live clients are
ready, by "Fixtures mode" in the running app, and by both data agents' test
suites.

Two functions live here, one per data agent:

- ``fixture_macro_df()`` (riksbank-data-expert) — Riksbank-shaped long data for
  all 7 ``RIKSBANK_SERIES`` codes.
- ``fixture_market_df()`` (yahoo-data-expert) — Yahoo-shaped long data for all
  19 ``ALL_TICKERS``.

Each agent owns and edits only their own function; append, don't overwrite.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from swedish_macro_dashboard.constants import (
    ALL_TICKERS,
    RIKSBANK_SERIES,
    SECTOR_BY_TICKER,
)
from swedish_macro_dashboard.data.schema import validate

_SEED = 42

# Plausible current-day-ish level per series, used as the base for a small
# deterministic trend + noise walk. Not real data - just realistic shapes.
_BASE_LEVEL = {
    "SEQGDPNAYSA": 2.0,
    "SEQGDPGAPYSA": 0.0,
    "SEQLABUEASA": 7.5,
    "SEMCPIFNAYNA": 2.0,
    "SEMCPIFFEXYNA": 2.0,
    "SEQRATENAYNA": 2.5,
    "SEDMENAYNA": 2.5,
}


def fixture_macro_df() -> pd.DataFrame:
    """Return a fake but schema-conformant long-format macro DataFrame.

    Covers all 7 ``RIKSBANK_SERIES`` codes with at least 40 quarterly / 120
    monthly actual rows each, plus a handful of trailing ``is_forecast=True``
    rows per series. Deterministic (seeded) and makes no network calls.
    """
    rng = np.random.default_rng(_SEED)
    now = datetime.now(UTC)
    end = pd.Timestamp.today().normalize()

    frames = []
    for code, meta in RIKSBANK_SERIES.items():
        freq = meta["frequency"]
        if freq == "Q":
            n_actual, n_forecast, pd_freq = 44, 6, "QS"
        else:
            n_actual, n_forecast, pd_freq = 130, 8, "MS"

        total = n_actual + n_forecast
        dates = pd.date_range(end=end, periods=total, freq=pd_freq)

        base = _BASE_LEVEL.get(code, 1.0)
        trend = np.linspace(-0.5, 0.5, total)
        noise = rng.normal(0, 0.3, size=total)
        values = base + trend + noise

        is_forecast = np.array([False] * n_actual + [True] * n_forecast)

        frames.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "metric_id": code,
                    "value": values,
                    "unit": meta["unit"],
                    "category": meta["category"],
                    "source": "riksbank",
                    "frequency": freq,
                    "last_updated": now,
                    "is_forecast": is_forecast,
                    "is_stale": False,
                }
            )
        )

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values(["metric_id", "date"]).reset_index(drop=True)
    return validate(result, name="fixture_macro_df")


# Plausible current SEK closing level per ticker, used as the base for a
# small deterministic random walk. Not real prices - just realistic shapes.
_BASE_PRICE = {
    "VOLV-B.ST": 280.0,
    "ATCO-A.ST": 620.0,
    "SAND.ST": 210.0,
    "SKF-B.ST": 220.0,
    "ALFA.ST": 480.0,
    "ASSA-B.ST": 340.0,
    "ERIC-B.ST": 75.0,
    "TELIA.ST": 30.0,
    "HEXA-B.ST": 130.0,
    "SEB-A.ST": 160.0,
    "SWED-A.ST": 220.0,
    "NDA-SE.ST": 130.0,
    "SHB-A.ST": 110.0,
    "INVE-B.ST": 310.0,
    "EQT.ST": 340.0,
    "HM-B.ST": 180.0,
    "ESSITY-B.ST": 290.0,
    "BOL.ST": 340.0,
    "EVO.ST": 800.0,
}

_N_DAILY_ROWS = 260  # ~ 1 trading year, comfortably over the 250-row minimum
_N_INTRADAY_BARS = 100  # one session of 5-minute bars (~09:00-17:30 CET)


def fixture_market_df() -> pd.DataFrame:
    """Return a fake but schema-conformant long-format market DataFrame.

    Covers all 19 ``ALL_TICKERS`` with at least 250 daily rows each
    (frequency ``"D"``) plus one session of 5-minute intraday rows each
    (frequency ``"5min"``). Deterministic (seeded) and makes no network
    calls. Values are a plausible SEK-denominated random walk, not real
    prices.
    """
    now = datetime.now(UTC)
    daily_end = pd.Timestamp.today().normalize()
    daily_dates = pd.bdate_range(end=daily_end, periods=_N_DAILY_ROWS)

    # One current-session's worth of 5-minute bars, distinct from any daily
    # timestamp (daily rows are at midnight; intraday rows carry a time).
    session_start = pd.Timestamp.today().normalize() + pd.Timedelta(hours=9)
    intraday_dates = pd.date_range(start=session_start, periods=_N_INTRADAY_BARS, freq="5min")

    frames = []
    for i, ticker in enumerate(ALL_TICKERS):
        sector = SECTOR_BY_TICKER[ticker]
        base = _BASE_PRICE.get(ticker, 100.0)
        rng = np.random.default_rng(_SEED + i)

        daily_returns = rng.normal(0, 0.01, size=_N_DAILY_ROWS)
        daily_values = base * np.cumprod(1 + daily_returns)

        frames.append(
            pd.DataFrame(
                {
                    "date": daily_dates,
                    "metric_id": ticker,
                    "value": daily_values,
                    "unit": "SEK",
                    "category": sector,
                    "source": "yahoo",
                    "frequency": "D",
                    "last_updated": now,
                    "is_forecast": False,
                    "is_stale": False,
                }
            )
        )

        intraday_returns = rng.normal(0, 0.002, size=_N_INTRADAY_BARS)
        intraday_values = daily_values[-1] * np.cumprod(1 + intraday_returns)

        frames.append(
            pd.DataFrame(
                {
                    "date": intraday_dates,
                    "metric_id": ticker,
                    "value": intraday_values,
                    "unit": "SEK",
                    "category": sector,
                    "source": "yahoo",
                    "frequency": "5min",
                    "last_updated": now,
                    "is_forecast": False,
                    "is_stale": False,
                }
            )
        )

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values(["metric_id", "date"]).reset_index(drop=True)
    return validate(result, name="fixture_market_df")
