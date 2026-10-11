"""Macro Overview tab (docs/SPEC.md F1 / docs/UI_PLAN.md Section 2 / docs/CHART_SPECS.md C1-C2).

Renders the state-of-the-cycle summary tile plus 7 category-grouped tiles.
Uses only macro_transforms + RIKSBANK_SERIES metadata; no data cleaning or
transform math happens in this module.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from swedish_macro_dashboard.constants import RIKSBANK_SERIES
from swedish_macro_dashboard.tabs import components
from swedish_macro_dashboard.transforms import macro_transforms

# Category display order per docs/UI_PLAN.md Section 2.
CATEGORY_ORDER = ["Growth", "Labour", "Prices", "Policy"]

POLICY_RATE_SERIES = "SEQRATENAYNA"


def _fmt_with_unit(value: float | None, unit_suffix: str) -> str:
    formatted = components.format_number(value)
    return formatted if formatted == "n/a" else f"{formatted} {unit_suffix}"


def render(macro_df: pd.DataFrame, show_forecasts: bool) -> None:
    """Render the full Macro Overview tab body."""
    if macro_df is None or macro_df.empty:
        components.unavailable_message("Macro (Riksbank)")
        return

    _render_cycle_tile(macro_df)

    latest = macro_transforms.latest_actuals(macro_df)
    latest_by_id = latest.set_index("metric_id") if not latest.empty else latest

    for category in CATEGORY_ORDER:
        codes = [code for code, meta in RIKSBANK_SERIES.items() if meta["category"] == category]
        if not codes:
            continue
        st.subheader(category)
        cols = st.columns(len(codes))
        for col, code in zip(cols, codes):
            with col:
                _render_tile(macro_df, latest_by_id, code, show_forecasts)


def _render_cycle_tile(macro_df: pd.DataFrame) -> None:
    _, rationale = macro_transforms.cycle_state(macro_df)
    gaps = macro_transforms.gap_metrics(macro_df)

    policy_rate = None
    latest = macro_transforms.latest_actuals(macro_df)
    if not latest.empty:
        policy_row = latest[latest["metric_id"] == POLICY_RATE_SERIES]
        if not policy_row.empty:
            policy_rate = float(policy_row["value"].iloc[0])

    stale = bool(macro_df["is_stale"].any())

    with st.container(border=True):
        rationale_text = rationale
        if stale:
            rationale_text += " (based on last saved data — stale)"
        st.write(rationale_text)

        chip_cols = st.columns(4)
        chip_cols[0].metric("GDP gap", _fmt_with_unit(gaps["gdp_gap"], "pp"))
        chip_cols[1].metric("CPIF gap vs 2%", _fmt_with_unit(gaps["cpif_gap"], "pp"))
        chip_cols[2].metric(
            "CPIF ex-energy gap vs 2%", _fmt_with_unit(gaps["cpif_ex_energy_gap"], "pp")
        )
        chip_cols[3].metric("Policy rate", _fmt_with_unit(policy_rate, "%"))


def _render_tile(
    macro_df: pd.DataFrame,
    latest_by_id: pd.DataFrame,
    code: str,
    show_forecasts: bool,
) -> None:
    meta = RIKSBANK_SERIES[code]
    with st.container(border=True):
        st.markdown(f"**{meta['description']}**")

        has_latest = (not latest_by_id.empty) and (code in latest_by_id.index)
        if not has_latest:
            st.caption("No data available")
            return

        row = latest_by_id.loc[code]
        latest_value = float(row["value"])
        latest_date = pd.Timestamp(row["date"])
        row_is_stale = bool(row["is_stale"])

        st.metric(meta["unit"], f"{latest_value:.2f}")
        st.caption(f"As of {latest_date:%Y-%m-%d}")

        actual_series = macro_transforms.sparkline_series(macro_df, code, include_forecast=False)
        if actual_series.empty:
            st.caption("No sparkline data")
            return

        forecast_series = None
        if show_forecasts:
            forecast_series = _forecast_segment(macro_df, code, actual_series)

        fig = components.sparkline_figure(actual_series, forecast_series, row_is_stale)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        span_caption = f"Actuals since {actual_series.index.min():%Y-%m-%d}"
        if row_is_stale:
            span_caption += " · stale"
        st.caption(span_caption)


def _forecast_segment(macro_df: pd.DataFrame, code: str, actual_series: pd.Series) -> pd.Series:
    """Forecast-only tail for `code`, prefixed with the last actual point so
    the dashed segment visually connects to the solid one (docs/CHART_SPECS.md C1).
    """
    forecast_rows = macro_df[(macro_df["metric_id"] == code) & (macro_df["is_forecast"])]
    forecast_rows = forecast_rows.sort_values("date")
    forecast_series = forecast_rows.set_index("date")["value"]

    if actual_series.empty or forecast_series.empty:
        return forecast_series

    connector = pd.Series([actual_series.iloc[-1]], index=[actual_series.index[-1]])
    return pd.concat([connector, forecast_series])
