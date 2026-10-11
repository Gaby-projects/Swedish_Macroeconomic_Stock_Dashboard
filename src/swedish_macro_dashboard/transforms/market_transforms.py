"""Market-level transforms consumed by the Stock Market tab (F2) and by
macro-market-expert's overlay_transforms (``equal_weighted_sector_index``).

Every function here takes an already-cleaned data.schema.COMMON_COLUMNS
frame (as returned by yahoo_client.fetch_market_data() or
fixtures.fixture_market_df()) and never re-cleans it, per docs/SPEC.md 3.5.

Return-window conventions (per docs/CHART_SPECS.md C3): windows are computed on
daily, non-forecast rows only (``frequency == "D"`` and ``is_forecast ==
False``) - intraday rows are excluded. A window with insufficient history
yields NaN, never 0 or a raised exception.
"""

from __future__ import annotations

import pandas as pd

from swedish_macro_dashboard.constants import ALL_TICKERS, TICKERS_BY_SECTOR

# Trading-day lookback counts for the fixed windows in docs/CHART_SPECS.md C3/C5.
# YTD is handled separately (calendar-year based, not a fixed row count).
_TRADING_DAY_WINDOWS = {
    "1d": 1,
    "1w": 5,
    "1m": 21,
    "1y": 252,
}


def _daily_actuals(market_df: pd.DataFrame) -> pd.DataFrame:
    """Daily, non-forecast rows only. Market rows are never forecasts in
    practice, but the filter is kept explicit per docs/SPEC.md's acceptance
    criteria ("Returns are computed from is_forecast=False daily rows only").
    """
    if market_df.empty:
        return market_df
    return market_df[(market_df["frequency"] == "D") & (~market_df["is_forecast"])]


def _close_pivot(daily_df: pd.DataFrame, metric_ids: list[str] | None = None) -> pd.DataFrame:
    """Wide date x metric_id pivot of close values, sorted by date."""
    columns = metric_ids or []
    if daily_df.empty:
        return pd.DataFrame(columns=columns)

    df = daily_df
    if metric_ids is not None:
        df = df[df["metric_id"].isin(metric_ids)]
    if df.empty:
        return pd.DataFrame(columns=columns)

    pivot = df.pivot_table(index="date", columns="metric_id", values="value", aggfunc="last")
    return pivot.sort_index()


def _trailing_return(series: pd.Series, periods: int) -> float:
    """% return from `periods` rows back to the latest value. NaN if the
    series doesn't have enough history for that window.
    """
    s = series.dropna()
    if len(s) <= periods:
        return float("nan")
    past = s.iloc[-1 - periods]
    latest = s.iloc[-1]
    if pd.isna(past) or pd.isna(latest) or past == 0:
        return float("nan")
    return (latest / past - 1.0) * 100.0


def _ytd_return(series: pd.Series) -> float:
    """% return from the first observation of the latest observed calendar
    year to the latest observation. NaN if there's only one point (or
    none) in that year.
    """
    s = series.dropna()
    if s.empty:
        return float("nan")
    latest_year = s.index[-1].year
    this_year = s[s.index.year == latest_year]
    if len(this_year) < 2:
        return float("nan")
    past = this_year.iloc[0]
    latest = this_year.iloc[-1]
    if pd.isna(past) or pd.isna(latest) or past == 0:
        return float("nan")
    return (latest / past - 1.0) * 100.0


def latest_price(market_df: pd.DataFrame, metric_id: str) -> float:
    """Latest daily close (non-forecast) for one ticker or sector-index id.
    NaN if there is no daily data for it.
    """
    daily = _daily_actuals(market_df)
    if daily.empty:
        return float("nan")
    subset = daily[daily["metric_id"] == metric_id].sort_values("date")
    if subset.empty:
        return float("nan")
    return float(subset["value"].iloc[-1])


def rebase_to_100(
    market_df: pd.DataFrame,
    tickers: list[str],
    start: pd.Timestamp | str | None = None,
) -> pd.DataFrame:
    """Wide date x ticker frame for the sector chart's per-ticker lines (C4).

    Each ticker's daily close series is independently rebased to 100 at its
    own first available date on/after `start` (or its first date overall
    when `start` is None). Tickers with no data in the window are simply
    absent as an all-NaN column, per docs/CHART_SPECS.md ("omitted from the
    chart... named in a caption") - callers decide the caption text.
    """
    daily = _daily_actuals(market_df)
    pivot = _close_pivot(daily, tickers)

    if start is not None:
        pivot = pivot[pivot.index >= pd.Timestamp(start)]

    rebased = pd.DataFrame(index=pivot.index, columns=pivot.columns, dtype=float)
    for col in pivot.columns:
        s = pivot[col].dropna()
        if s.empty:
            continue
        base = s.iloc[0]
        if pd.isna(base) or base == 0:
            continue
        rebased[col] = (pivot[col] / base) * 100.0
    return rebased


def equal_weighted_sector_index(market_df: pd.DataFrame, sector: str) -> pd.Series:
    """Equal-weighted index for `sector`'s tickers, date-indexed, rebased to
    100 at its first common date (daily close rows only).

    A sector with a single ticker returns that ticker's own rebase-to-100
    series unchanged, per the acceptance criteria in docs/tasks.json T015.
    """
    tickers = TICKERS_BY_SECTOR.get(sector, [])
    if not tickers:
        return pd.Series(dtype=float, name=sector)

    daily = _daily_actuals(market_df)
    pivot = _close_pivot(daily, tickers)
    pivot = pivot.dropna(axis=1, how="all")
    if pivot.empty:
        return pd.Series(dtype=float, name=sector)

    # Dates where every remaining constituent has a value - the sector
    # index's "first common date" per its own contract.
    common = pivot.dropna(how="any")
    if common.empty:
        # No single date has all constituents (e.g. staggered listing
        # history) - fall back to the raw pivot; skipna averaging below
        # still produces a sensible equal-weighted line over whichever
        # constituents are available at each date.
        common = pivot

    base = common.iloc[0]
    rebased = common.divide(base) * 100.0
    index_series = rebased.mean(axis=1, skipna=True)
    index_series.name = sector
    return index_series


def returns_table(market_df: pd.DataFrame, tickers: list[str] | None = None) -> pd.DataFrame:
    """One row per ticker: latest close plus 1d/1w/1m/YTD/1y % returns
    (docs/CHART_SPECS.md C3). Missing windows are NaN, not 0; row order matches
    `tickers` (defaults to ALL_TICKERS, i.e. TICKERS_BY_SECTOR iteration order).
    """
    tickers = tickers or ALL_TICKERS
    daily = _daily_actuals(market_df)
    pivot = _close_pivot(daily, tickers)

    rows = []
    for ticker in tickers:
        series = pivot[ticker].dropna() if ticker in pivot.columns else pd.Series(dtype=float)
        rows.append(
            {
                "metric_id": ticker,
                "latest_close": series.iloc[-1] if not series.empty else float("nan"),
                "1d": _trailing_return(series, _TRADING_DAY_WINDOWS["1d"]),
                "1w": _trailing_return(series, _TRADING_DAY_WINDOWS["1w"]),
                "1m": _trailing_return(series, _TRADING_DAY_WINDOWS["1m"]),
                "YTD": _ytd_return(series),
                "1y": _trailing_return(series, _TRADING_DAY_WINDOWS["1y"]),
            }
        )
    return pd.DataFrame(rows)


def sector_returns_table(market_df: pd.DataFrame) -> pd.DataFrame:
    """One row per sector: latest equal-weighted index level plus
    1d/1w/1m/YTD/1y % returns (docs/CHART_SPECS.md C3, "All sectors" sub-tab).
    Row order matches `TICKERS_BY_SECTOR` keys.
    """
    rows = []
    for sector in TICKERS_BY_SECTOR:
        series = equal_weighted_sector_index(market_df, sector).dropna()
        rows.append(
            {
                "sector": sector,
                "latest_index_level": series.iloc[-1] if not series.empty else float("nan"),
                "1d": _trailing_return(series, _TRADING_DAY_WINDOWS["1d"]),
                "1w": _trailing_return(series, _TRADING_DAY_WINDOWS["1w"]),
                "1m": _trailing_return(series, _TRADING_DAY_WINDOWS["1m"]),
                "YTD": _ytd_return(series),
                "1y": _trailing_return(series, _TRADING_DAY_WINDOWS["1y"]),
            }
        )
    return pd.DataFrame(rows)
