"""Macro x Market overlay tab (docs/SPEC.md F3 / docs/UI_PLAN.md Section 4 / docs/CHART_SPECS.md C7-C8).

Uses only overlay_transforms for the merge/alignment/correlation math; this
module only renders the selectors, the dual-axis chart and the readouts.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from swedish_macro_dashboard.constants import (
    ALL_TICKERS,
    RIKSBANK_SERIES,
    TICKERS_BY_SECTOR,
)
from swedish_macro_dashboard.tabs import components
from swedish_macro_dashboard.transforms import overlay_transforms

WINDOW_OPTIONS = ["1y", "3y", "5y", "max"]

MACRO_OPTIONS = list(RIKSBANK_SERIES.keys())
MARKET_OPTIONS = [f"Index: {sector}" for sector in TICKERS_BY_SECTOR] + list(ALL_TICKERS)

DEFAULT_MACRO = "SEQGDPNAYSA"
DEFAULT_MARKET = "Index: Banking"


def _macro_label(code: str) -> str:
    return RIKSBANK_SERIES.get(code, {}).get("description", code)


def _resolve_market_id(choice: str) -> str:
    prefix = "Index: "
    return choice.removeprefix(prefix)


def render(macro_df: pd.DataFrame, market_df: pd.DataFrame) -> None:
    """Render the full Macro x Market tab body."""
    macro_available = macro_df is not None and not macro_df.empty
    market_available = market_df is not None and not market_df.empty

    st.write(
        "Overlay one macro series against one market series (a ticker or a sector index) "
        "on a shared date axis."
    )

    st.session_state.setdefault("mm_macro", DEFAULT_MACRO)
    st.session_state.setdefault("mm_market", DEFAULT_MARKET)
    st.session_state.setdefault("mm_window", "1y")

    pair_col1, pair_col2 = st.columns(2)
    pairs_disabled = not (macro_available and market_available)
    if pair_col1.button("Policy rate vs. Banking index", disabled=pairs_disabled):
        st.session_state["mm_macro"] = "SEQRATENAYNA"
        st.session_state["mm_market"] = "Index: Banking"
    if pair_col2.button("CPIF vs. H&M", disabled=pairs_disabled):
        st.session_state["mm_macro"] = "SEMCPIFNAYNA"
        st.session_state["mm_market"] = "HM-B.ST"

    col1, col2, col3 = st.columns(3)
    with col1:
        macro_choice = st.selectbox(
            "Macro series",
            MACRO_OPTIONS,
            format_func=_macro_label,
            disabled=not macro_available,
            key="mm_macro",
        )
    with col2:
        market_choice = st.selectbox(
            "Market series",
            MARKET_OPTIONS,
            disabled=not market_available,
            key="mm_market",
        )
    with col3:
        window = st.radio(
            "Window", WINDOW_OPTIONS, key="mm_window", horizontal=True, disabled=pairs_disabled
        )

    if not macro_available and not market_available:
        st.warning(
            "Macro and market data unavailable — live fetch failed and no snapshot exists. "
            "Try Refresh now."
        )
        return
    if not macro_available:
        components.unavailable_message("Macro (Riksbank)")
        return
    if not market_available:
        components.unavailable_message("Market (Yahoo Finance)")
        return

    market_id = _resolve_market_id(market_choice)
    try:
        frame = overlay_transforms.overlay_frame(macro_df, market_df, macro_choice, market_id, window)
    except ValueError:
        st.warning("Unable to build this overlay - selection is not a known ticker or sector.")
        return

    if frame.empty:
        st.warning("No overlapping data available for this combination and window.")
        return

    fig = _build_overlay_chart(frame)
    st.plotly_chart(fig, use_container_width=True)


def _build_overlay_chart(frame: pd.DataFrame) -> go.Figure:
    macro_is_stale = bool(frame["macro_is_stale"].iloc[0])
    market_is_stale = bool(frame["market_is_stale"].iloc[0])

    macro_description = frame["macro_description"].iloc[0]
    macro_unit = frame["macro_unit"].iloc[0]
    market_label = frame["market_label"].iloc[0]

    macro_axis_title = f"{macro_description} ({macro_unit})" + (" (stale)" if macro_is_stale else "")
    market_axis_title = market_label + (" (stale)" if market_is_stale else "")

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame["macro_value"],
            name=macro_description,
            yaxis="y1",
            line={"color": components.ACCENT_COLOR, "dash": "dash" if macro_is_stale else "solid"},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame["market_value"],
            name=market_label,
            yaxis="y2",
            line={"color": "#ff7f0e", "dash": "dash" if market_is_stale else "solid"},
        )
    )
    fig.update_layout(
        xaxis={"title": "Date"},
        yaxis={"title": macro_axis_title},
        yaxis2={"title": market_axis_title, "overlaying": "y", "side": "right"},
        legend={"orientation": "h", "yanchor": "bottom", "y": -0.3},
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        height=450,
    )
    return fig
