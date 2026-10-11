"""Shared rendering helpers used across tabs/*.py.

Nothing here fetches data or computes transforms - it only renders DataFrames
/ scalars that callers already obtained from riksbank_client, yahoo_client or
transforms/*.py, per CLAUDE.md ownership rules.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Colour convention (docs/CHART_SPECS.md "Cross-cutting rules" + docs/UI_PLAN.md Section 1):
# green = live, amber = stale snapshot, red = unavailable, grey = fixtures mode.
STATUS_COLORS = {
    "live": "#66BB6A",
    "stale": "#FFB300",
    "unavailable": "#EF5350",
    "fixtures": "#9e9e9e",
}

ACCENT_COLOR = "#4FC3F7"
STALE_LINE_COLOR = "#9e9e9e"


def source_status(df: pd.DataFrame | None, fixtures_mode: bool) -> str:
    """One of 'fixtures' / 'unavailable' / 'stale' / 'live', per docs/UI_PLAN.md Section 1."""
    if fixtures_mode:
        return "fixtures"
    if df is None or df.empty:
        return "unavailable"
    if "is_stale" in df.columns and bool(df["is_stale"].any()):
        return "stale"
    return "live"


def freshness_badge(df: pd.DataFrame | None, source_label: str, fixtures_mode: bool) -> None:
    """Render one sidebar freshness badge (colour dot + last_updated + caption)."""
    status = source_status(df, fixtures_mode)
    color = STATUS_COLORS[status]

    if status == "fixtures":
        updated_text = "n/a (fixture data)"
        caption = "Fixtures mode - network disabled"
    elif status == "unavailable":
        updated_text = "n/a"
        caption = "No data available. Live fetch failed and no snapshot exists."
    else:
        last_updated = df["last_updated"].max()
        if pd.isna(last_updated):
            updated_text = "n/a"
        else:
            updated_text = pd.Timestamp(last_updated).strftime("Updated %H:%M on %Y-%m-%d")
        if status == "stale":
            caption = f"Showing last saved snapshot from {last_updated}."
        else:
            caption = "Live."

    st.markdown(
        f"<span style='display:inline-block;width:10px;height:10px;border-radius:50%;"
        f"background-color:{color};margin-right:6px;'></span>"
        f"<strong>{source_label}</strong> &mdash; {updated_text}",
        unsafe_allow_html=True,
    )
    st.caption(caption)


def unavailable_message(source_label: str) -> None:
    """Render the standard 'unavailable' warning for a tab body (docs/UI_PLAN.md Section 5)."""
    st.warning(
        f"{source_label} data unavailable — live fetch failed and no snapshot exists. "
        "Try Refresh now."
    )


def stale_suffix(is_stale: bool, tag: str = "⚠") -> str:
    """Small inline suffix for a table cell/legend entry that came from a stale snapshot."""
    return f" {tag}" if is_stale else ""


def sparkline_figure(
    actual: pd.Series,
    forecast: pd.Series | None,
    is_stale: bool,
    height: int = 90,
) -> go.Figure:
    """Compact sparkline (no axes/ticks) with an optional dashed forecast tail.

    Per docs/CHART_SPECS.md C1: solid accent line for actuals (grey if is_stale),
    dashed lower-opacity continuation for forecast rows when supplied.
    """
    color = STALE_LINE_COLOR if is_stale else ACCENT_COLOR
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=actual.index,
            y=actual.to_numpy(),
            mode="lines",
            line={"color": color, "width": 2},
            hoverinfo="skip",
            showlegend=False,
        )
    )
    if forecast is not None and not forecast.empty:
        fig.add_trace(
            go.Scatter(
                x=forecast.index,
                y=forecast.to_numpy(),
                mode="lines",
                line={"color": color, "width": 2, "dash": "dash"},
                opacity=0.5,
                hoverinfo="skip",
                showlegend=False,
            )
        )
    fig.update_layout(
        height=height,
        margin={"l": 0, "r": 0, "t": 2, "b": 2},
        xaxis={"visible": False},
        yaxis={"visible": False},
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    return fig


def format_pct(value: float | None) -> str:
    """'n/a' for NaN/None, otherwise a signed 2dp percentage - never 0 for missing data."""
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:+.2f}%"


def format_number(value: float | None, decimals: int = 2) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:,.{decimals}f}"
