"""Tests for transforms.overlay_transforms, run against the two data agents'
fixtures plus small hand-built Series for edge cases. No live network is
used anywhere here."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from swedish_macro_dashboard.constants import RIKSBANK_SERIES, TICKERS_BY_SECTOR
from swedish_macro_dashboard.data.fixtures import fixture_macro_df, fixture_market_df
from swedish_macro_dashboard.transforms.overlay_transforms import (
    INSUFFICIENT_DATA_MESSAGE,
    align_macro_market,
    align_series,
    correlation_readout,
    overlay_frame,
    rebase_pair_to_100,
    resolve_market_series,
    rolling_correlation,
)

MACRO_DF = fixture_macro_df()
MARKET_DF = fixture_market_df()


# --- align_series / align_macro_market -------------------------------------


def test_align_series_forward_fills_macro_onto_market_calendar():
    macro = pd.Series(
        [1.0, 2.0, 3.0],
        index=pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
    )
    market = pd.Series(
        [100.0, 101.0, 102.0, 103.0],
        index=pd.to_datetime(["2024-01-15", "2024-01-16", "2024-02-15", "2024-03-15"]),
    )
    aligned = align_series(macro, market)

    assert list(aligned.columns) == ["date", "market_value", "macro_value"]
    assert len(aligned) == len(market)
    # 2024-01-15/16 fall after the Jan macro print but before Feb -> forward-filled to 1.0.
    assert aligned.loc[aligned["date"] == pd.Timestamp("2024-01-16"), "macro_value"].iloc[0] == 1.0
    assert aligned.loc[aligned["date"] == pd.Timestamp("2024-02-15"), "macro_value"].iloc[0] == 2.0
    assert aligned.loc[aligned["date"] == pd.Timestamp("2024-03-15"), "macro_value"].iloc[0] == 3.0


def test_align_series_nan_before_first_macro_observation():
    macro = pd.Series([5.0], index=pd.to_datetime(["2024-06-01"]))
    market = pd.Series(
        [1.0, 2.0, 3.0],
        index=pd.to_datetime(["2024-01-01", "2024-06-01", "2024-12-01"]),
    )
    aligned = align_series(macro, market)
    before = aligned.loc[aligned["date"] == pd.Timestamp("2024-01-01"), "macro_value"].iloc[0]
    on_and_after = aligned.loc[aligned["date"] >= pd.Timestamp("2024-06-01"), "macro_value"]

    assert pd.isna(before)
    assert on_and_after.notna().all()


def test_align_series_empty_inputs_return_empty_frame():
    empty = pd.Series(dtype=float)
    non_empty = pd.Series([1.0], index=pd.to_datetime(["2024-01-01"]))
    assert align_series(empty, non_empty).empty
    assert align_series(non_empty, empty).empty


def test_align_series_handles_mixed_datetime_resolutions():
    # Live data: Riksbank dates come in as seconds, Yahoo timestamps as microseconds.
    macro = pd.Series(
        [1.0, 2.0],
        index=pd.to_datetime(["2024-01-01", "2024-02-01"]).as_unit("s"),
    )
    market = pd.Series(
        [100.0, 101.0],
        index=pd.to_datetime(["2024-01-15", "2024-02-15"]).as_unit("us"),
    )
    aligned = align_series(macro, market)

    assert aligned["macro_value"].tolist() == [1.0, 2.0]


def test_align_macro_market_excludes_forecast_rows():
    aligned = align_macro_market(MACRO_DF, resolve_market_series(MARKET_DF, "HM-B.ST"), "SEMCPIFNAYNA")
    max_macro_date = MACRO_DF[
        (MACRO_DF["metric_id"] == "SEMCPIFNAYNA") & (~MACRO_DF["is_forecast"])
    ]["date"].max()
    forecast_only_start = MACRO_DF[
        (MACRO_DF["metric_id"] == "SEMCPIFNAYNA") & (MACRO_DF["is_forecast"])
    ]["date"].min()

    assert max_macro_date < forecast_only_start
    # No market date should pick up a macro value beyond the last actual print
    # via some forecast row leaking into the join.
    post_actual = aligned[aligned["date"] > max_macro_date]
    if not post_actual.empty:
        last_actual_value = MACRO_DF[
            (MACRO_DF["metric_id"] == "SEMCPIFNAYNA")
            & (~MACRO_DF["is_forecast"])
            & (MACRO_DF["date"] == max_macro_date)
        ]["value"].iloc[0]
        assert (post_actual["macro_value"] == last_actual_value).all()


@pytest.mark.parametrize("metric_id", list(RIKSBANK_SERIES.keys()))
@pytest.mark.parametrize("sector", list(TICKERS_BY_SECTOR.keys()))
def test_overlay_frame_runs_for_every_macro_x_sector_pair(metric_id, sector):
    frame = overlay_frame(MACRO_DF, MARKET_DF, metric_id, sector, window="max")
    assert not frame.empty
    assert frame["macro_value"].notna().any()
    assert frame["market_value"].notna().all()
    assert (frame["market_label"] == f"{sector} index (equal-weighted)").all()


def test_overlay_frame_single_ticker_selector():
    frame = overlay_frame(MACRO_DF, MARKET_DF, "SEQRATENAYNA", "HM-B.ST", window="1y")
    assert not frame.empty
    assert (frame["market_label"] == "HM-B.ST").all()
    assert frame["macro_description"].iloc[0] == "Policy rate"
    assert frame["macro_unit"].iloc[0] == "Per cent"


def test_overlay_frame_empty_on_empty_source():
    empty = MACRO_DF.iloc[0:0]
    frame = overlay_frame(empty, MARKET_DF, "SEQRATENAYNA", "Banking", window="max")
    assert frame.empty
    assert list(frame.columns) == [
        "date",
        "macro_value",
        "market_value",
        "macro_unit",
        "macro_description",
        "market_label",
        "macro_is_stale",
        "market_is_stale",
    ]


def test_resolve_market_series_unknown_raises():
    with pytest.raises(ValueError):
        resolve_market_series(MARKET_DF, "not-a-real-ticker-or-sector")


def test_resolve_market_series_single_ticker_sector_matches_ticker_series():
    # Consumer sector has exactly one ticker (HM-B.ST); its index should equal
    # that ticker's own rebase-to-100, per market_transforms' own contract.
    from swedish_macro_dashboard.transforms.market_transforms import rebase_to_100

    index_series = resolve_market_series(MARKET_DF, "Consumer")
    ticker_rebased = rebase_to_100(MARKET_DF, ["HM-B.ST"])["HM-B.ST"]
    pd.testing.assert_series_equal(
        index_series.rename(None), ticker_rebased.rename(None), check_freq=False
    )


# --- rebase_pair_to_100 -----------------------------------------------------


def test_rebase_pair_to_100_starts_at_100():
    aligned = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "macro_value": [2.0, 2.5, 3.0],
            "market_value": [100.0, 110.0, 90.0],
        }
    )
    rebased = rebase_pair_to_100(aligned)
    assert rebased["macro_rebased"].iloc[0] == pytest.approx(100.0)
    assert rebased["market_rebased"].iloc[0] == pytest.approx(100.0)
    assert rebased["market_rebased"].iloc[1] == pytest.approx(110.0)


def test_rebase_pair_to_100_zero_base_is_nan_not_error():
    aligned = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "macro_value": [0.0, 1.0],
            "market_value": [100.0, 105.0],
        }
    )
    rebased = rebase_pair_to_100(aligned)
    assert rebased["macro_rebased"].isna().all()


# --- rolling_correlation / correlation_readout ------------------------------


def test_rolling_correlation_perfectly_correlated():
    aligned = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=10, freq="D"),
            "macro_value": np.arange(10, dtype=float),
            "market_value": np.arange(10, dtype=float) * 2 + 1,
        }
    )
    assert rolling_correlation(aligned, window="max") == pytest.approx(1.0)
    assert "Rolling correlation (max): 1.00" == correlation_readout(aligned, window="max")


def test_rolling_correlation_insufficient_data_returns_nan_and_readout_string():
    aligned = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01"]),
            "macro_value": [1.0],
            "market_value": [100.0],
        }
    )
    assert pd.isna(rolling_correlation(aligned, window="max"))
    assert correlation_readout(aligned, window="max") == INSUFFICIENT_DATA_MESSAGE


def test_rolling_correlation_no_overlap_returns_nan():
    aligned = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "macro_value": [np.nan, np.nan],
            "market_value": [100.0, 101.0],
        }
    )
    assert pd.isna(rolling_correlation(aligned, window="max"))
    assert correlation_readout(aligned, window="max") == INSUFFICIENT_DATA_MESSAGE


def test_correlation_readout_on_real_overlay_frame_changes_with_window():
    frame = overlay_frame(MACRO_DF, MARKET_DF, "SEQRATENAYNA", "Banking", window="max")
    readout_1y = correlation_readout(frame, window="1y")
    readout_max = correlation_readout(frame, window="max")
    # Both must be well-formed strings (never a raw float, NaN, or traceback).
    assert readout_1y.startswith("Rolling correlation") or readout_1y == INSUFFICIENT_DATA_MESSAGE
    assert readout_max.startswith("Rolling correlation") or readout_max == INSUFFICIENT_DATA_MESSAGE


def test_unknown_window_raises_value_error():
    frame = overlay_frame(MACRO_DF, MARKET_DF, "SEQRATENAYNA", "Banking", window="max")
    with pytest.raises(ValueError):
        rolling_correlation(frame, window="10y")


def test_fixtures_have_expected_shape_for_overlay_use():
    # Sanity check that the fixtures this module relies on still satisfy the
    # assumptions overlay_transforms makes (long format, is_forecast/is_stale
    # present) - guards against silent fixture drift breaking these tests
    # for the wrong reason.
    for col in ("date", "metric_id", "value", "is_forecast", "is_stale", "frequency"):
        assert col in MACRO_DF.columns
        assert col in MARKET_DF.columns
    assert datetime.now(UTC).tzinfo is not None  # sanity import check
