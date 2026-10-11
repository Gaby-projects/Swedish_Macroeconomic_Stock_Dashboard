"""Entry point for the Swedish Macro + Stock Market dashboard.

Thin assembler only: page config, sidebar controls / freshness badges, and
the three tabs described in docs/UI_PLAN.md. Data fetching lives in
data/riksbank_client.py and data/yahoo_client.py; transform math lives in
transforms/*.py; this file only wires cached calls to those into the tab
renderers under src/swedish_macro_dashboard/tabs/.

Run with: uv run streamlit run app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from swedish_macro_dashboard.data import fixtures, riksbank_client, yahoo_client
from swedish_macro_dashboard.tabs import (
    components,
    macro_market,
    macro_overview,
    stock_market,
)

MACRO_TTL_SECONDS = 6 * 60 * 60  # 6 hours, per docs/SPEC.md 2.1 / F5
MARKET_TTL_SECONDS = 15 * 60  # 15 minutes, per docs/SPEC.md 2.2 / F5

# `.stTabs .stTabs` targets the sector sub-tabs nested inside the Stock Market tab.
TAB_STYLES = """
<style>
.stTabs [role="tablist"] { gap: 0.5rem; }
.stTabs [role="tab"] {
    height: auto;
    padding: 0.6rem 1.4rem;
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 0.5rem 0.5rem 0 0;
    background-color: rgba(255, 255, 255, 0.05);
}
.stTabs [role="tab"] p { font-size: 1.3rem; font-weight: 600; }
.stTabs [role="tab"][aria-selected="true"] {
    border-color: #4FC3F7;
    background-color: rgba(79, 195, 247, 0.18);
}
.stTabs .stTabs [role="tablist"] { gap: 0.35rem; flex-wrap: wrap; }
.stTabs .stTabs [role="tab"] { padding: 0.4rem 1rem; }
.stTabs .stTabs [role="tab"] p { font-size: 1.05rem; }
</style>
"""


@st.cache_data(ttl=MACRO_TTL_SECONDS, show_spinner=False)
def _cached_fetch_macro() -> pd.DataFrame:
    return riksbank_client.fetch_macro_series()


@st.cache_data(ttl=MARKET_TTL_SECONDS, show_spinner=False)
def _cached_fetch_market() -> pd.DataFrame:
    # range=10y/interval=1d is the "long view" call per docs/SPEC.md 2.2; the
    # per-ticker intraday (1d/5m) strip in the Stock Market tab is fetched
    # separately, per docs/CHART_SPECS.md C6's own input spec.
    return yahoo_client.fetch_market_data(range_="10y", interval="1d")


def _load_data(fixtures_mode: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    if fixtures_mode:
        return fixtures.fixture_macro_df(), fixtures.fixture_market_df()
    return _cached_fetch_macro(), _cached_fetch_market()


def _render_sidebar(macro_df: pd.DataFrame, market_df: pd.DataFrame, fixtures_mode: bool) -> bool:
    """Render sidebar controls/badges. Returns the "show forecasts" toggle state."""
    with st.sidebar:
        st.title("Swedish Macroeconomic indicators + Stock Market")

        refresh_clicked = st.button(
            "Refresh now", disabled=fixtures_mode, use_container_width=True
        )
        if refresh_clicked:
            with st.spinner("Refreshing data…"):
                _cached_fetch_macro.clear()
                _cached_fetch_market.clear()
            st.rerun()

        st.divider()
        components.freshness_badge(macro_df, "Riksbank", fixtures_mode)
        st.divider()
        components.freshness_badge(market_df, "Yahoo", fixtures_mode)
        st.divider()

        st.toggle("Fixtures mode", key="fixtures_mode")
        show_forecasts = st.toggle("Show forecasts", key="show_forecasts")

        st.divider()
        st.caption("Riksbank Monetary Policy Data API · Yahoo Finance chart API")
        st.caption("Local/demo tool — not investment advice.")

    return show_forecasts


def main() -> None:
    st.set_page_config(
        page_title="Overview of macroeconomic indicators for Swedish economy and Swedish equities",
        layout="wide",
    )

    # Read before rendering the toggle widget itself, so the very first load
    # of this run already reflects the persisted choice (see docs/UI_PLAN.md
    # Section 1 point 4).
    fixtures_mode = st.session_state.get("fixtures_mode", False)
    macro_df, market_df = _load_data(fixtures_mode)

    show_forecasts = _render_sidebar(macro_df, market_df, fixtures_mode)

    st.markdown(TAB_STYLES, unsafe_allow_html=True)
    st.title("Overview of macroeconomic indicators for Swedish economy and Swedish equities")
    if fixtures_mode:
        st.caption("Fixtures mode — showing sample data, network disabled.")

    tab_macro, tab_stock, tab_overlay = st.tabs(
        ["Macro Overview", "Stock Market", "Macro × Market"]
    )

    with tab_macro:
        macro_overview.render(macro_df, show_forecasts)

    with tab_stock:
        stock_market.render(market_df, fixtures_mode)

    with tab_overlay:
        macro_market.render(macro_df, market_df)


main()
