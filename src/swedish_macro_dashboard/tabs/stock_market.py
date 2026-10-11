"""Stock Market tab (docs/SPEC.md F2 / docs/UI_PLAN.md Section 3 / docs/CHART_SPECS.md C3-C6).

Renders 8 sector sub-tabs plus "All sectors". Uses only market_transforms +
TICKERS_BY_SECTOR metadata for the tables/charts; the per-ticker intraday
strip additionally calls yahoo_client.get_ticker_history directly, per
docs/CHART_SPECS.md C6's own input spec, wrapped in a short-TTL st.cache_data so a
normal rerun doesn't re-hit the network for all 19 tickers.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from swedish_macro_dashboard.constants import TICKERS_BY_SECTOR
from swedish_macro_dashboard.data import yahoo_client
from swedish_macro_dashboard.tabs import components
from swedish_macro_dashboard.transforms import market_transforms

LOOKBACK_OPTIONS = ["1m", "6m", "1y", "5y", "max"]
LOOKBACK_DAYS = {"1m": 30, "6m": 182, "1y": 365, "5y": 365 * 5}

SECTOR_ORDER = list(TICKERS_BY_SECTOR.keys())
INTRADAY_TTL_SECONDS = 15 * 60


def render(market_df: pd.DataFrame, fixtures_mode: bool) -> None:
    """Render the full Stock Market tab body."""
    if market_df is None or market_df.empty:
        components.unavailable_message("Market (Yahoo Finance)")
        return

    lookback = st.radio(
        "Lookback", LOOKBACK_OPTIONS, index=0, horizontal=True, key="stock_lookback"
    )
    start = _lookback_start(market_df, lookback)

    tab_labels = SECTOR_ORDER + ["All sectors"]
    tabs = st.tabs(tab_labels)

    for tab, sector in zip(tabs[:-1], SECTOR_ORDER):
        with tab:
            _render_sector(market_df, sector, start, fixtures_mode)

    with tabs[-1]:
        _render_all_sectors(market_df, start)


def _lookback_start(market_df: pd.DataFrame, lookback: str) -> pd.Timestamp | None:
    if lookback == "max":
        return None
    daily = market_df[market_df["frequency"] == "D"]
    if daily.empty:
        return None
    max_date = daily["date"].max()
    return max_date - pd.Timedelta(days=LOOKBACK_DAYS[lookback])


def _stale_by_metric(daily_df: pd.DataFrame, metric_ids: list[str]) -> dict[str, bool]:
    """Whether the latest daily row for each metric_id came from a snapshot."""
    subset = daily_df[daily_df["metric_id"].isin(metric_ids)]
    if subset.empty:
        return {}
    latest_idx = subset.groupby("metric_id")["date"].idxmax()
    latest_rows = subset.loc[latest_idx]
    return dict(zip(latest_rows["metric_id"], latest_rows["is_stale"]))


def _format_table(
    df: pd.DataFrame,
    id_col: str,
    id_label: str,
    price_col: str,
    price_label: str,
    stale_map: dict[str, bool],
) -> pd.DataFrame:
    out = pd.DataFrame()
    out[id_label] = df[id_col].apply(lambda x: f"{x}{components.stale_suffix(stale_map.get(x, False))}")
    out[price_label] = df[price_col].apply(components.format_number)
    for window in ["1d", "1w", "1m", "YTD", "1y"]:
        out[f"{window} %"] = df[window].apply(components.format_pct)
    return out


def _render_sector(
    market_df: pd.DataFrame, sector: str, start: pd.Timestamp | None, fixtures_mode: bool
) -> None:
    tickers = TICKERS_BY_SECTOR[sector]

    daily = market_df[market_df["frequency"] == "D"]
    stale_map = _stale_by_metric(daily, tickers)

    table = market_transforms.returns_table(market_df, tickers)
    display = _format_table(table, "metric_id", "Ticker", "latest_close", "Latest close (SEK)", stale_map)
    st.dataframe(display, hide_index=True, width="stretch")

    _sector_chart(market_df, sector, tickers, start, stale_map)

    st.caption("Last session (5-minute bars)")
    _intraday_strip(tickers, market_df, fixtures_mode)


def _sector_chart(
    market_df: pd.DataFrame,
    sector: str,
    tickers: list[str],
    start: pd.Timestamp | None,
    stale_map: dict[str, bool],
) -> None:
    rebased = market_transforms.rebase_to_100(market_df, tickers, start=start)
    index_series = market_transforms.equal_weighted_sector_index(market_df, sector)
    if start is not None and not index_series.empty:
        index_series = index_series[index_series.index >= start]
        # The index is rebased over its full history; restart it at 100 on the
        # window's first day so it shares a baseline with the ticker lines.
        if not index_series.empty:
            index_series = index_series / index_series.iloc[0] * 100

    if rebased.empty and index_series.empty:
        st.info("No market data available for this sector in the selected window.")
        return

    palette = px.colors.qualitative.Plotly
    fig = go.Figure()
    missing: list[str] = []

    for i, ticker in enumerate(tickers):
        series = rebased[ticker].dropna() if ticker in rebased.columns else pd.Series(dtype=float)
        if series.empty:
            missing.append(ticker)
            continue
        is_stale = bool(stale_map.get(ticker, False))
        name = f"{ticker}{' (stale)' if is_stale else ''}"
        fig.add_trace(
            go.Scatter(
                x=series.index,
                y=series.to_numpy(),
                mode="lines",
                name=name,
                line={"color": palette[i % len(palette)], "width": 1.5, "dash": "dash" if is_stale else "solid"},
            )
        )

    if not index_series.empty:
        fig.add_trace(
            go.Scatter(
                x=index_series.index,
                y=index_series.to_numpy(),
                mode="lines",
                name="Sector index (equal-weighted)",
                line={"color": "#FFFFFF", "width": 3},
            )
        )

    fig.update_layout(
        yaxis_title="Rebased to 100",
        legend={"orientation": "h", "yanchor": "bottom", "y": -0.4},
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        height=380,
    )
    st.plotly_chart(fig, use_container_width=True)

    if missing:
        st.caption(f"No data in this window for: {', '.join(missing)}")


def _render_all_sectors(market_df: pd.DataFrame, start: pd.Timestamp | None) -> None:
    table = market_transforms.sector_returns_table(market_df)
    display = _format_table(
        table, "sector", "Sector", "latest_index_level", "Latest index level", {}
    )
    st.dataframe(display, hide_index=True, width="stretch")

    palette = px.colors.qualitative.Set2
    fig = go.Figure()
    missing: list[str] = []

    for i, sector in enumerate(SECTOR_ORDER):
        series = market_transforms.equal_weighted_sector_index(market_df, sector)
        if start is not None and not series.empty:
            series = series[series.index >= start]
        series = series.dropna()
        if series.empty:
            missing.append(sector)
            continue
        series = series / series.iloc[0] * 100  # restart at 100 on the window's first day
        fig.add_trace(
            go.Scatter(
                x=series.index,
                y=series.to_numpy(),
                mode="lines",
                name=sector,
                line={"color": palette[i % len(palette)], "width": 2},
            )
        )

    fig.update_layout(
        yaxis_title="Rebased to 100",
        legend={"orientation": "h", "yanchor": "bottom", "y": -0.4},
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        height=420,
    )
    st.plotly_chart(fig, use_container_width=True)

    if missing:
        st.caption(f"No data in this window for: {', '.join(missing)}")


@st.cache_data(ttl=INTRADAY_TTL_SECONDS, show_spinner=False)
def _cached_intraday(ticker: str) -> pd.DataFrame:
    return yahoo_client.get_ticker_history(ticker, range_="1d", interval="5m")


def _get_intraday(ticker: str, market_df: pd.DataFrame, fixtures_mode: bool) -> pd.DataFrame:
    """Intraday close series for one ticker, as (date, close) rows.

    In fixtures mode this reads the 5min rows already embedded in
    `fixture_market_df()` (no network); live mode calls
    `get_ticker_history` directly (that function has no snapshot fallback of
    its own, so a network failure here degrades to the "Market closed"
    placeholder rather than a traceback, per docs/UI_PLAN.md Section 5).
    """
    if fixtures_mode:
        rows = market_df[(market_df["metric_id"] == ticker) & (market_df["frequency"] == "5min")]
        if rows.empty:
            return pd.DataFrame(columns=["date", "close"])
        rows = rows.sort_values("date")
        return rows[["date", "value"]].rename(columns={"value": "close"})

    try:
        history = _cached_intraday(ticker)
    except Exception:  # noqa: BLE001 - never let one ticker's failure crash the tab
        return pd.DataFrame(columns=["date", "close"])
    if history is None or history.empty:
        return pd.DataFrame(columns=["date", "close"])
    return history[["date", "close"]]


def _intraday_strip(tickers: list[str], market_df: pd.DataFrame, fixtures_mode: bool) -> None:
    cols = st.columns(len(tickers))
    for col, ticker in zip(cols, tickers):
        with col:
            _intraday_tile(ticker, market_df, fixtures_mode)


def _intraday_tile(ticker: str, market_df: pd.DataFrame, fixtures_mode: bool) -> None:
    st.caption(ticker)
    history = _get_intraday(ticker, market_df, fixtures_mode)

    if history.empty:
        st.markdown(
            "<div style='height:60px;background:rgba(255,255,255,0.08);display:flex;align-items:center;"
            "justify-content:center;color:#9AA8BA;font-size:0.8em;border-radius:4px;'>"
            "Market closed</div>",
            unsafe_allow_html=True,
        )
        return

    series = history.set_index("date")["close"]
    is_stale = fixtures_mode is False and bool(
        market_df[
            (market_df["metric_id"] == ticker) & (market_df["frequency"] == "5min")
        ]["is_stale"].any()
    )

    if is_stale:
        color = components.STALE_LINE_COLOR
        dash = "dash"
    else:
        up = bool(series.iloc[-1] >= series.iloc[0])
        color = "#66BB6A" if up else "#EF5350"
        dash = "solid"

    fig = go.Figure(
        go.Scatter(
            x=series.index,
            y=series.to_numpy(),
            mode="lines",
            line={"color": color, "width": 2, "dash": dash},
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        height=60,
        margin={"l": 0, "r": 0, "t": 2, "b": 2},
        xaxis={"visible": False},
        yaxis={"visible": False},
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    if is_stale:
        st.caption("stale")
