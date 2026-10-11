"""Tests for transforms.macro_transforms, run against the fixture macro data."""

from __future__ import annotations

from swedish_macro_dashboard.constants import RIKSBANK_SERIES
from swedish_macro_dashboard.data.fixtures import fixture_macro_df
from swedish_macro_dashboard.transforms.macro_transforms import (
    CYCLE_LABELS,
    cycle_state,
    gap_metrics,
    latest_actuals,
    period_over_period_change,
    sparkline_series,
)


def test_latest_actuals_one_row_per_series_and_not_forecast():
    df = fixture_macro_df()
    result = latest_actuals(df)

    assert set(result["metric_id"]) == set(RIKSBANK_SERIES.keys())
    assert len(result) == len(RIKSBANK_SERIES)
    assert (result["is_forecast"] == False).all()


def test_latest_actuals_picks_the_max_actual_date_per_series():
    df = fixture_macro_df()
    result = latest_actuals(df)

    actuals = df[~df["is_forecast"]]
    for _, row in result.iterrows():
        expected = actuals[actuals["metric_id"] == row["metric_id"]]["date"].max()
        assert row["date"] == expected


def test_sparkline_series_excludes_forecast_by_default():
    df = fixture_macro_df()
    series = sparkline_series(df, "SEMCPIFNAYNA")

    forecast_dates = set(
        df[(df["metric_id"] == "SEMCPIFNAYNA") & df["is_forecast"]]["date"]
    )
    assert not forecast_dates & set(series.index)
    assert series.index.is_monotonic_increasing


def test_sparkline_series_can_include_forecast():
    df = fixture_macro_df()
    with_forecast = sparkline_series(df, "SEMCPIFNAYNA", include_forecast=True)
    without_forecast = sparkline_series(df, "SEMCPIFNAYNA", include_forecast=False)

    assert len(with_forecast) > len(without_forecast)


def test_period_over_period_change_is_a_float():
    df = fixture_macro_df()
    change = period_over_period_change(df, "SEQGDPNAYSA")
    assert isinstance(change, float)


def test_gap_metrics_returns_one_float_per_series():
    df = fixture_macro_df()
    gaps = gap_metrics(df)

    assert set(gaps.keys()) == {"gdp_gap", "cpif_gap", "cpif_ex_energy_gap"}
    for value in gaps.values():
        assert isinstance(value, float)

    cpif_latest = df[(df["metric_id"] == "SEMCPIFNAYNA") & (~df["is_forecast"])].sort_values(
        "date"
    )["value"].iloc[-1]
    assert gaps["cpif_gap"] == cpif_latest - 2.0


def test_cycle_state_returns_known_label_and_rationale():
    df = fixture_macro_df()
    label, rationale = cycle_state(df)

    assert label in CYCLE_LABELS
    assert isinstance(rationale, str)
    assert len(rationale) > 0


def test_cycle_state_is_deterministic_on_the_fixture():
    df = fixture_macro_df()
    first = cycle_state(df)
    second = cycle_state(fixture_macro_df())

    assert first == second
