"""Macro x Market overlay transforms (F3 / docs/CHART_SPECS.md C7-C8).

This module is the "so what" layer between the two source-specific transform
modules: it takes one already-cleaned macro series (from ``macro_df`` via
``macro_transforms``) and one already-cleaned market series (a raw ticker
close-price series, or a pre-computed sector index from
``market_transforms.equal_weighted_sector_index``) and produces an aligned,
comparable frame plus a correlation "so what" stat.

See ``docs/OVERLAY_NOTES.md`` (repo root) for the alignment strategy, its
tradeoffs, and the exact definition of the correlation stat below. Do not
change the alignment direction (market calendar as the join target) without
updating that file - docs/CHART_SPECS.md C7 pins "market calendar" as the x-axis.

This module never re-implements logic owned by the other transform modules:
- GDP gap / CPIF gap / state-of-cycle / per-series actuals -> ``macro_transforms``.
- Sector index / returns / rebasing -> ``market_transforms``.
"""

from __future__ import annotations

import pandas as pd

from swedish_macro_dashboard.constants import (
    ALL_TICKERS,
    RIKSBANK_SERIES,
    TICKERS_BY_SECTOR,
)
from swedish_macro_dashboard.transforms import macro_transforms, market_transforms

# docs/CHART_SPECS.md C7's window selector (1y / 3y / 5y / max). "max" means no
# slicing at all. Calendar-day approximations (not trading-day counts) since
# the window is applied to the *market* calendar's date column.
WINDOW_DAYS = {"1y": 365, "3y": 365 * 3, "5y": 365 * 5}

# Exact phrase pinned by docs/CHART_SPECS.md C8's missing-data rule - streamlit
# builder matches on this string, so it must not be reworded here.
INSUFFICIENT_DATA_MESSAGE = "n/a — insufficient overlapping data"


# --------------------------------------------------------------------------
# Generic pairing (point 3 of the brief): works on any two date-indexed
# value Series, regardless of where they came from.
# --------------------------------------------------------------------------


def align_series(macro: pd.Series, market: pd.Series) -> pd.DataFrame:
    """Align any macro value series onto any market value series' calendar.

    Forward-fill strategy: for every date in ``market``'s index, attach the
    most recent ``macro`` observation at or before that date
    (``pd.merge_asof(..., direction="backward")``). See docs/OVERLAY_NOTES.md for
    why this direction (market calendar as the join target) was chosen over
    downsampling the market series to the macro frequency.

    Both inputs must be date-indexed ``pd.Series`` of floats (a raw ticker
    close-price series or a ``market_transforms.equal_weighted_sector_index``
    output work identically here - the market side is opaque past "a
    date-indexed float Series").

    Returns a frame with columns ``date``, ``macro_value``, ``market_value``,
    one row per date in ``market``'s index. Rows before the first macro
    observation have ``macro_value = NaN``; every row from the first macro
    observation onward is non-null (assuming ``market`` itself has no gaps
    in the requested rows).
    """
    macro_frame = macro.rename("macro_value").reset_index()
    macro_frame.columns = ["date", "macro_value"]
    macro_frame = macro_frame.dropna(subset=["date"]).sort_values("date")

    market_frame = market.rename("market_value").reset_index()
    market_frame.columns = ["date", "market_value"]
    market_frame = market_frame.dropna(subset=["date"]).sort_values("date")

    if macro_frame.empty or market_frame.empty:
        return pd.DataFrame(columns=["date", "macro_value", "market_value"])

    # pandas 3 keeps each source's datetime resolution (Riksbank dates parse as
    # seconds, Yahoo timestamps as microseconds) and merge_asof refuses to join
    # mismatched resolutions, so align both on nanoseconds first.
    macro_frame["date"] = macro_frame["date"].astype("datetime64[ns]")
    market_frame["date"] = market_frame["date"].astype("datetime64[ns]")

    return pd.merge_asof(market_frame, macro_frame, on="date", direction="backward")


# --------------------------------------------------------------------------
# Frame-based entry points (pinned names from docs/CHART_SPECS.md / docs/tasks.json)
# --------------------------------------------------------------------------


def align_macro_market(
    macro_df: pd.DataFrame, market_series: pd.Series, metric_id: str
) -> pd.DataFrame:
    """``align_series`` for the common case: macro side resolved from
    ``macro_df`` by ``metric_id``, market side already resolved by the
    caller (a ticker's close series or a sector index series).

    Forecast rows are excluded via ``macro_transforms.sparkline_series``
    (``include_forecast=False``) before alignment, per docs/SPEC.md 2.3 /
    docs/CHART_SPECS.md C7 ("Forecast rows are excluded entirely from this
    trace").
    """
    macro_series = macro_transforms.sparkline_series(macro_df, metric_id, include_forecast=False)
    return align_series(macro_series, market_series)


def resolve_market_series(market_df: pd.DataFrame, ticker_or_sector: str) -> pd.Series:
    """Resolve the market leg of an overlay from either a single ticker or a
    sector name, returning a date-indexed value Series in both cases.

    - Sector name (a key of ``TICKERS_BY_SECTOR``): delegates to
      ``market_transforms.equal_weighted_sector_index`` - already rebased to
      100 at its first date, per that function's own contract.
    - Ticker (a member of ``ALL_TICKERS``): the raw daily close price in SEK,
      non-forecast rows only, no rebasing.

    Raises ``ValueError`` for anything else rather than silently returning
    an empty series, since an unresolvable selector is a caller bug (e.g. a
    stale UI selector), not a legitimate empty-data state.
    """
    if ticker_or_sector in TICKERS_BY_SECTOR:
        return market_transforms.equal_weighted_sector_index(market_df, ticker_or_sector)
    if ticker_or_sector in ALL_TICKERS:
        return _ticker_close_series(market_df, ticker_or_sector)
    raise ValueError(f"{ticker_or_sector!r} is not a known ticker or sector name")


def _ticker_close_series(market_df: pd.DataFrame, ticker: str) -> pd.Series:
    """Raw daily close-price series (SEK) for one ticker, non-forecast rows
    only. Trivial schema-contract filtering only (frequency == "D" and
    ~is_forecast, per docs/SPEC.md 3.1) - not a reimplementation of anything in
    ``market_transforms`` (which has no public "single raw ticker series"
    accessor; its exports compute returns/rebasing/sector aggregates).
    """
    if market_df.empty:
        return pd.Series(dtype=float, name=ticker)
    daily = market_df[
        (market_df["metric_id"] == ticker)
        & (market_df["frequency"] == "D")
        & (~market_df["is_forecast"])
    ].sort_values("date")
    if daily.empty:
        return pd.Series(dtype=float, name=ticker)
    return daily.set_index("date")["value"].rename(ticker)


def _market_label(ticker_or_sector: str) -> str:
    """Axis/legend label per docs/CHART_SPECS.md C7: the ticker symbol as-is, or
    "{Sector} index (equal-weighted)" for a sector index.
    """
    if ticker_or_sector in TICKERS_BY_SECTOR:
        return f"{ticker_or_sector} index (equal-weighted)"
    return ticker_or_sector


def _window_start(end: pd.Timestamp, window: str | None) -> pd.Timestamp | None:
    if window is None or window == "max":
        return None
    if window not in WINDOW_DAYS:
        raise ValueError(f"Unknown window {window!r}; expected one of {sorted(WINDOW_DAYS)} or 'max'")
    return end - pd.Timedelta(days=WINDOW_DAYS[window])


def _slice_window(aligned: pd.DataFrame, window: str | None) -> pd.DataFrame:
    """Restrict an ``align_series``/``align_macro_market`` frame to the
    trailing ``window`` (1y/3y/5y/max), anchored on the frame's own latest
    market date (the market calendar is the alignment target, so the window
    is measured against it, not against "today").
    """
    if aligned.empty or "date" not in aligned.columns:
        return aligned
    start = _window_start(aligned["date"].max(), window)
    if start is None:
        return aligned.reset_index(drop=True)
    return aligned[aligned["date"] >= start].reset_index(drop=True)


def _macro_is_stale(macro_df: pd.DataFrame, metric_id: str, start: pd.Timestamp | None) -> bool:
    if macro_df.empty:
        return False
    subset = macro_df[(macro_df["metric_id"] == metric_id) & (~macro_df["is_forecast"])]
    if start is not None:
        subset = subset[subset["date"] >= start]
    return bool(subset["is_stale"].any()) if not subset.empty else False


def _market_is_stale(
    market_df: pd.DataFrame, ticker_or_sector: str, start: pd.Timestamp | None
) -> bool:
    if market_df.empty:
        return False
    tickers = TICKERS_BY_SECTOR.get(ticker_or_sector, [ticker_or_sector])
    subset = market_df[
        market_df["metric_id"].isin(tickers)
        & (market_df["frequency"] == "D")
        & (~market_df["is_forecast"])
    ]
    if start is not None:
        subset = subset[subset["date"] >= start]
    return bool(subset["is_stale"].any()) if not subset.empty else False


def overlay_frame(
    macro_df: pd.DataFrame,
    market_df: pd.DataFrame,
    macro_metric_id: str,
    market_series_id: str,
    window: str | None = "max",
) -> pd.DataFrame:
    """Build the exact frame docs/CHART_SPECS.md C7's dual-axis chart consumes.

    One row per market date within ``window``, with:
    - ``date``, ``macro_value``, ``market_value`` (see ``align_series``).
    - ``macro_unit``, ``macro_description`` (from ``RIKSBANK_SERIES``, for
      the left-axis title).
    - ``market_label`` (ticker symbol, or "{Sector} index (equal-weighted)",
      for the right-axis title).
    - ``macro_is_stale``, ``market_is_stale`` (bool, constant per column):
      True if any contributing row in the window came from a stale
      snapshot, per C7's "that leg's line is drawn dashed" rule.

    Returns an empty frame (no columns assumed beyond the ones above) if
    either source frame is empty, per C7's empty-state rule - the caller
    (streamlit-builder) renders the unavailable message instead of charting
    this.
    """
    columns = [
        "date",
        "macro_value",
        "market_value",
        "macro_unit",
        "macro_description",
        "market_label",
        "macro_is_stale",
        "market_is_stale",
    ]
    if macro_df.empty or market_df.empty:
        return pd.DataFrame(columns=columns)

    market_series = resolve_market_series(market_df, market_series_id)
    aligned = align_macro_market(macro_df, market_series, macro_metric_id)
    aligned = _slice_window(aligned, window)
    if aligned.empty:
        return pd.DataFrame(columns=columns)

    start = aligned["date"].min()
    meta = RIKSBANK_SERIES.get(macro_metric_id, {})
    aligned = aligned.assign(
        macro_unit=meta.get("unit", ""),
        macro_description=meta.get("description", macro_metric_id),
        market_label=_market_label(market_series_id),
        macro_is_stale=_macro_is_stale(macro_df, macro_metric_id, start),
        market_is_stale=_market_is_stale(market_df, market_series_id, start),
    )
    return aligned[columns]


# --------------------------------------------------------------------------
# Normalization helper (not used by C7 itself - see docs/OVERLAY_NOTES.md - but
# required generically per the brief, and available for any future
# normalized-overlay view).
# --------------------------------------------------------------------------


def rebase_pair_to_100(aligned_df: pd.DataFrame) -> pd.DataFrame:
    """Rebase ``macro_value`` and ``market_value`` to 100 at the first
    non-null observation of each, so the two series (different units) are
    visually comparable on one shared axis.

    Adds ``macro_rebased`` / ``market_rebased`` columns; leaves the original
    ``macro_value`` / ``market_value`` columns untouched. A column whose
    first valid value is 0 or entirely NaN rebases to all-NaN rather than
    dividing by zero.
    """
    df = aligned_df.copy()
    for value_col, out_col in (("macro_value", "macro_rebased"), ("market_value", "market_rebased")):
        series = df.get(value_col)
        if series is None:
            df[out_col] = float("nan")
            continue
        valid = series.dropna()
        base = valid.iloc[0] if not valid.empty else float("nan")
        if pd.isna(base) or base == 0:
            df[out_col] = float("nan")
        else:
            df[out_col] = (series / base) * 100.0
    return df


# --------------------------------------------------------------------------
# The "so what" stat (docs/CHART_SPECS.md C8).
# --------------------------------------------------------------------------


def rolling_correlation(aligned_df: pd.DataFrame, window: str | None = "max") -> float:
    """Pearson correlation of ``macro_value`` vs. ``market_value`` over the
    trailing ``window`` (1y/3y/5y/max - same options as C7's selector).

    "Rolling" here means "computed over the trailing window", i.e. a single
    scalar for whichever window is currently selected - not a time-series of
    correlations. See docs/OVERLAY_NOTES.md for why. Computed pairwise-complete
    (rows where either value is NaN, e.g. before the first macro
    observation, are dropped first). Returns ``float('nan')`` if fewer than
    2 overlapping points remain, never raises.
    """
    sliced = _slice_window(aligned_df, window) if "date" in aligned_df.columns else aligned_df
    if not {"macro_value", "market_value"}.issubset(sliced.columns):
        return float("nan")
    pairs = sliced[["macro_value", "market_value"]].dropna()
    if len(pairs) < 2:
        return float("nan")
    corr = pairs["macro_value"].corr(pairs["market_value"])
    return float(corr) if pd.notna(corr) else float("nan")


def correlation_readout(aligned_df: pd.DataFrame, window: str | None = "max") -> str:
    """Plain-language readout backing C8's ``st.metric``, e.g.
    ``"Rolling correlation (1y): 0.42"``. Returns the exact
    ``INSUFFICIENT_DATA_MESSAGE`` string (never ``NaN`` or a stack trace)
    when there are fewer than 2 overlapping points in the window, per
    docs/CHART_SPECS.md C8's missing-data rule.
    """
    sliced = _slice_window(aligned_df, window) if "date" in aligned_df.columns else aligned_df
    if not {"macro_value", "market_value"}.issubset(sliced.columns):
        return INSUFFICIENT_DATA_MESSAGE
    pairs = sliced[["macro_value", "market_value"]].dropna()
    if len(pairs) < 2:
        return INSUFFICIENT_DATA_MESSAGE
    corr = rolling_correlation(aligned_df, window)
    window_label = window if window else "max"
    return f"Rolling correlation ({window_label}): {corr:.2f}"
