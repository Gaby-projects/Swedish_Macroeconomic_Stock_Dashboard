"""Tests for transforms.market_transforms, run against the fixture market data
plus a few small hand-built frames for edge cases. No live network is used
anywhere here."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from swedish_macro_dashboard.constants import ALL_TICKERS, TICKERS_BY_SECTOR
from swedish_macro_dashboard.data.fixtures import fixture_market_df
from swedish_macro_dashboard.data.schema import COMMON_COLUMNS
from swedish_macro_dashboard.transforms.market_transforms import (
    equal_weighted_sector_index,
    latest_price,
    rebase_to_100,
    returns_table,
    sector_returns_table,
)


def _row(date, metric_id, value, frequency="D", is_forecast=False, category="Consumer"):
    return {
        "date": pd.Timestamp(date),
        "metric_id": metric_id,
        "value": value,
        "unit": "SEK",
        "category": category,
        "source": "yahoo",
        "frequency": frequency,
        "last_updated": datetime.now(UTC),
        "is_forecast": is_forecast,
        "is_stale": False,
    }


# --- equal_weighted_sector_index -------------------------------------------


def test_equal_weighted_sector_index_rebased_to_100_at_first_date():
    df = fixture_market_df()
    for sector in TICKERS_BY_SECTOR:
        index = equal_weighted_sector_index(df, sector)
        assert not index.empty
        assert index.iloc[0] == pytest.approx(100.0)
        assert index.index.is_monotonic_increasing


def test_equal_weighted_sector_index_single_ticker_sector_matches_ticker_rebase():
    df = fixture_market_df()
    # "Consumer" has exactly one constituent (HM-B.ST) per constants.py.
    ticker = TICKERS_BY_SECTOR["Consumer"][0]
    index = equal_weighted_sector_index(df, "Consumer")
    rebased = rebase_to_100(df, [ticker])[ticker].dropna()

    pd.testing.assert_series_equal(
        index.sort_index(), rebased.sort_index(), check_names=False
    )


def test_equal_weighted_sector_index_unknown_sector_returns_empty_series():
    df = fixture_market_df()
    index = equal_weighted_sector_index(df, "Not A Real Sector")
    assert index.empty


def test_equal_weighted_sector_index_empty_input_returns_empty_series():
    empty = pd.DataFrame(columns=COMMON_COLUMNS)
    index = equal_weighted_sector_index(empty, "Banking")
    assert index.empty


# --- returns_table / sector_returns_table -----------------------------------


def test_returns_table_covers_all_tickers_with_expected_columns():
    df = fixture_market_df()
    table = returns_table(df)

    assert set(table["metric_id"]) == set(ALL_TICKERS)
    for col in ("latest_close", "1d", "1w", "1m", "YTD", "1y"):
        assert col in table.columns


def test_returns_table_missing_window_is_nan_not_zero():
    # Only 3 daily rows - nowhere near enough for a 1y (252-row) or 1m
    # (21-row) window; those must come back NaN, never 0.
    rows = [
        _row("2024-01-01", "HM-B.ST", 100.0),
        _row("2024-01-02", "HM-B.ST", 101.0),
        _row("2024-01-03", "HM-B.ST", 102.0),
    ]
    df = pd.DataFrame(rows)

    table = returns_table(df, tickers=["HM-B.ST"])
    row = table.iloc[0]

    assert pd.isna(row["1m"])
    assert pd.isna(row["1y"])
    assert row["1d"] == pytest.approx((102.0 / 101.0 - 1.0) * 100.0)


def test_returns_table_ticker_with_no_data_is_all_nan():
    df = pd.DataFrame([_row("2024-01-01", "HM-B.ST", 100.0)])
    table = returns_table(df, tickers=["HM-B.ST", "EVO.ST"])

    evo_row = table[table["metric_id"] == "EVO.ST"].iloc[0]
    assert pd.isna(evo_row["latest_close"])
    assert pd.isna(evo_row["1d"])


def test_returns_excludes_intraday_and_forecast_rows():
    rows = [
        _row("2024-01-01", "HM-B.ST", 100.0, frequency="D"),
        _row("2024-01-02", "HM-B.ST", 999.0, frequency="5min"),  # excluded
        _row("2024-01-02", "HM-B.ST", 105.0, frequency="D", is_forecast=True),  # excluded
    ]
    df = pd.DataFrame(rows)

    table = returns_table(df, tickers=["HM-B.ST"])
    assert table.iloc[0]["latest_close"] == pytest.approx(100.0)


def test_sector_returns_table_covers_all_sectors():
    df = fixture_market_df()
    table = sector_returns_table(df)

    assert set(table["sector"]) == set(TICKERS_BY_SECTOR.keys())
    for col in ("latest_index_level", "1d", "1w", "1m", "YTD", "1y"):
        assert col in table.columns


# --- rebase_to_100 -----------------------------------------------------------


def test_rebase_to_100_starts_at_100_for_each_ticker():
    df = fixture_market_df()
    tickers = TICKERS_BY_SECTOR["Banking"]
    rebased = rebase_to_100(df, tickers)

    for ticker in tickers:
        first_value = rebased[ticker].dropna().iloc[0]
        assert first_value == pytest.approx(100.0)


def test_rebase_to_100_respects_start_date():
    df = fixture_market_df()
    ticker = "VOLV-B.ST"
    full = rebase_to_100(df, [ticker])
    mid_date = full.index[len(full) // 2]

    windowed = rebase_to_100(df, [ticker], start=mid_date)
    assert windowed.index.min() >= mid_date
    assert windowed[ticker].dropna().iloc[0] == pytest.approx(100.0)


# --- latest_price -------------------------------------------------------------


def test_latest_price_returns_most_recent_daily_close():
    df = pd.DataFrame(
        [
            _row("2024-01-01", "HM-B.ST", 100.0),
            _row("2024-01-02", "HM-B.ST", 105.0),
        ]
    )
    assert latest_price(df, "HM-B.ST") == pytest.approx(105.0)


def test_latest_price_unknown_ticker_is_nan():
    df = fixture_market_df()
    assert pd.isna(latest_price(df, "NOPE.ST"))
