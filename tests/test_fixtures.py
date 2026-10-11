"""Schema-conformance tests for data.fixtures.fixture_macro_df().

No live network is used anywhere here - fixture_macro_df() is pure
computation over constants.RIKSBANK_SERIES.
"""

from __future__ import annotations

import pandas as pd

from swedish_macro_dashboard.constants import (
    ALL_TICKERS,
    RIKSBANK_SERIES,
    SECTOR_BY_TICKER,
)
from swedish_macro_dashboard.data.fixtures import fixture_macro_df, fixture_market_df
from swedish_macro_dashboard.data.schema import COMMON_COLUMNS, validate


def test_fixture_macro_df_has_all_common_columns():
    df = fixture_macro_df()
    validate(df, name="fixture_macro_df")
    for col in COMMON_COLUMNS:
        assert col in df.columns


def test_fixture_macro_df_covers_all_series():
    df = fixture_macro_df()
    assert set(df["metric_id"].unique()) == set(RIKSBANK_SERIES.keys())


def test_fixture_macro_df_has_enough_history_per_frequency():
    df = fixture_macro_df()
    for code, meta in RIKSBANK_SERIES.items():
        subset = df[df["metric_id"] == code]
        if meta["frequency"] == "Q":
            assert len(subset) >= 40
        else:
            assert len(subset) >= 120


def test_fixture_macro_df_has_some_forecast_rows_per_series():
    df = fixture_macro_df()
    for code in RIKSBANK_SERIES:
        subset = df[df["metric_id"] == code]
        assert subset["is_forecast"].any()
        assert (~subset["is_forecast"]).any()


def test_fixture_macro_df_is_stale_always_false():
    df = fixture_macro_df()
    assert (df["is_stale"] == False).all()


def test_fixture_macro_df_no_nulls_in_required_columns():
    df = fixture_macro_df()
    assert df["date"].notna().all()
    assert df["metric_id"].notna().all()
    assert df["value"].notna().all()


def test_fixture_macro_df_no_duplicate_date_metric_pairs():
    df = fixture_macro_df()
    assert not df.duplicated(subset=["date", "metric_id"]).any()


def test_fixture_macro_df_sorted_by_metric_then_date():
    df = fixture_macro_df()
    expected = df.sort_values(["metric_id", "date"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(df.reset_index(drop=True), expected)


def test_fixture_macro_df_metadata_matches_constants():
    df = fixture_macro_df()
    for code, meta in RIKSBANK_SERIES.items():
        subset = df[df["metric_id"] == code]
        assert (subset["unit"] == meta["unit"]).all()
        assert (subset["category"] == meta["category"]).all()
        assert (subset["frequency"] == meta["frequency"]).all()
        assert (subset["source"] == "riksbank").all()


def test_fixture_macro_df_makes_no_network_call(monkeypatch):
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("fixture_macro_df() must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    fixture_macro_df()


# --- fixture_market_df() (yahoo-data-expert) -------------------------------


def test_fixture_market_df_has_all_common_columns():
    df = fixture_market_df()
    validate(df, name="fixture_market_df")
    for col in COMMON_COLUMNS:
        assert col in df.columns


def test_fixture_market_df_covers_all_tickers():
    df = fixture_market_df()
    assert set(df["metric_id"].unique()) == set(ALL_TICKERS)


def test_fixture_market_df_has_enough_daily_history_per_ticker():
    df = fixture_market_df()
    daily = df[df["frequency"] == "D"]
    for ticker in ALL_TICKERS:
        assert len(daily[daily["metric_id"] == ticker]) >= 250


def test_fixture_market_df_has_one_intraday_session_per_ticker():
    df = fixture_market_df()
    intraday = df[df["frequency"] == "5min"]
    for ticker in ALL_TICKERS:
        subset = intraday[intraday["metric_id"] == ticker]
        assert len(subset) > 1
        # Intraday timestamps must keep full time-of-day, not just midnight.
        assert (subset["date"].dt.time != pd.Timestamp("00:00:00").time()).all()


def test_fixture_market_df_is_stale_and_is_forecast_always_false():
    df = fixture_market_df()
    assert (df["is_stale"] == False).all()
    assert (df["is_forecast"] == False).all()


def test_fixture_market_df_no_nulls_in_required_columns():
    df = fixture_market_df()
    assert df["date"].notna().all()
    assert df["metric_id"].notna().all()
    assert df["value"].notna().all()


def test_fixture_market_df_no_duplicate_date_metric_pairs():
    df = fixture_market_df()
    assert not df.duplicated(subset=["date", "metric_id"]).any()


def test_fixture_market_df_metadata_matches_constants():
    df = fixture_market_df()
    for ticker in ALL_TICKERS:
        subset = df[df["metric_id"] == ticker]
        assert (subset["unit"] == "SEK").all()
        assert (subset["category"] == SECTOR_BY_TICKER[ticker]).all()
        assert (subset["source"] == "yahoo").all()


def test_fixture_market_df_sorted_by_metric_then_date():
    df = fixture_market_df()
    expected = df.sort_values(["metric_id", "date"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(df.reset_index(drop=True), expected)


def test_fixture_market_df_makes_no_network_call(monkeypatch):
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("fixture_market_df() must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    fixture_market_df()


def test_fixture_macro_df_still_unchanged_and_importable():
    # Guards against fixture_market_df() accidentally breaking the
    # riksbank-data-expert-owned function it shares a module with.
    df = fixture_macro_df()
    validate(df, name="fixture_macro_df")
    assert set(df["metric_id"].unique()) == set(RIKSBANK_SERIES.keys())
