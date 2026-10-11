"""Client for Yahoo Finance stock data (Swedish tickers).

Calls Yahoo's public chart API directly via `requests` rather than the
`yfinance` package: on this dev machine, yfinance's curl_cffi backend fails
SSL verification against a locally-trusted root CA (see docs/api-research.md).
Calling the chart endpoint directly avoids that dependency entirely.

fetch_market_data() output conforms to data.schema.COMMON_COLUMNS (value=close).
get_ticker_history() returns the richer raw OHLCV shape for detailed charts.

Hardening on top of the basic fetch:
- Per-ticker retry with exponential backoff (tenacity), independent per
  ticker so one failing ticker never blocks the others.
- On-disk snapshot fallback: a successful fetch overwrites
  ``<repo_root>/data_snapshots/yahoo_market.csv``; a ticker that still fails
  after retries falls back to its rows from that snapshot with
  ``is_stale=True``. If no snapshot rows exist for it, it is simply omitted
  (never raised).
- Cleaning (dedup, dtype coercion, null handling, sort) applied inside
  ``to_common_schema()`` and again on the assembled batch before the final
  ``schema.validate()``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from swedish_macro_dashboard.constants import ALL_TICKERS, SECTOR_BY_TICKER
from swedish_macro_dashboard.data.schema import COMMON_COLUMNS, validate

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Snapshot lives at the repo root, not inside the installed package, so it's
# easy to gitignore and doesn't get bundled into the package. Same format
# (CSV) and layout convention as riksbank_client.py's snapshot.
SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "data_snapshots"
SNAPSHOT_FILE = SNAPSHOT_DIR / "yahoo_market.csv"

_OHLCV_COLUMNS = [
    "date", "ticker", "sector", "open", "high", "low", "close", "volume",
    "currency", "frequency",
]

# Interval strings whose bars are daily or coarser (safe to normalize to
# midnight). Anything else (e.g. "5m", "15m", "1h") is intraday and must
# keep its full timestamp - normalizing those collapses a session's worth
# of 5-minute bars into a single day (the bug fixed here).
_DAILY_OR_COARSER_SUFFIXES = ("d", "wk", "mo")

logger = logging.getLogger(__name__)

_RETRYABLE = (requests.RequestException,)


def _is_daily_or_coarser(interval: str) -> bool:
    return interval.endswith(_DAILY_OR_COARSER_SUFFIXES)


def _empty_ohlcv_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_OHLCV_COLUMNS)


def get_ticker_history(ticker: str, range_: str = "1mo", interval: str = "1d") -> pd.DataFrame:
    """Fetch raw OHLCV history for one ticker (for the Stock Market tab's detailed charts).

    Intraday intervals (e.g. "5m") keep full timestamps and get
    frequency="5min"; daily-or-coarser intervals are normalized to midnight
    and get frequency="D". Returns an empty (but correctly-shaped) frame
    when Yahoo has no bars for the requested window (market closed /
    weekend / holiday / pre-open session) rather than raising.
    """
    resp = requests.get(
        CHART_URL.format(ticker=ticker),
        headers=HEADERS,
        params={"range": range_, "interval": interval},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()["chart"]

    if payload.get("error"):
        raise ValueError(f"{ticker}: {payload['error']}")

    results = payload.get("result") or []
    if not results:
        return _empty_ohlcv_frame()

    result = results[0]
    meta = result["meta"]
    timestamps = result.get("timestamp") or []
    if not timestamps:
        # No bars for this session yet (weekend, holiday, or pre-open) -
        # a legitimate "no data" response, not an error.
        return _empty_ohlcv_frame()

    quote = result["indicators"]["quote"][0]

    ts = (
        pd.to_datetime(timestamps, unit="s", utc=True)
        .tz_convert("Europe/Stockholm")
        .tz_localize(None)
    )
    if _is_daily_or_coarser(interval):
        dates = ts.normalize()
        frequency = "D"
    else:
        dates = ts
        frequency = "5min"

    df = pd.DataFrame({
        "date": dates,
        "open": quote["open"],
        "high": quote["high"],
        "low": quote["low"],
        "close": quote["close"],
        "volume": quote["volume"],
    })
    df["ticker"] = ticker
    df["sector"] = SECTOR_BY_TICKER.get(ticker)
    df["currency"] = meta["currency"]
    df["frequency"] = frequency

    return df[_OHLCV_COLUMNS]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the shared cleaning rules (docs/SPEC.md 3.5) before returning.

    Dedup on (date, metric_id) keeping the last row, dtype coercion, drop
    rows with a null date/metric_id/value - this also catches all-NaN
    ticker-days (e.g. halted trading), which fall out via the null-value
    drop rather than being passed through - and sort by metric_id then date.
    """
    df = df.copy()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["last_updated"] = pd.to_datetime(df["last_updated"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["metric_id"] = df["metric_id"].astype("string").astype(object)
    df["is_forecast"] = df["is_forecast"].fillna(False).astype(bool)
    df["is_stale"] = df["is_stale"].fillna(False).astype(bool)

    before = len(df)
    df = df.dropna(subset=["date", "metric_id", "value"])
    dropped = before - len(df)
    if dropped:
        logger.info(
            "yahoo_client: dropped %d row(s) with null date/metric_id/value "
            "(includes all-NaN ticker-days)",
            dropped,
        )

    df = df.drop_duplicates(subset=["date", "metric_id"], keep="last")
    df = df.sort_values(["metric_id", "date"]).reset_index(drop=True)
    return df


def to_common_schema(ohlcv_df: pd.DataFrame) -> pd.DataFrame:
    """Project a raw OHLCV frame (as from get_ticker_history) down to COMMON_COLUMNS.

    Cleans (dedup, dtype coercion, null handling, sort) before validating,
    per docs/SPEC.md 3.5 - not just a column-presence check.
    """
    if ohlcv_df.empty:
        return pd.DataFrame(columns=COMMON_COLUMNS)

    df = ohlcv_df.copy()
    df["metric_id"] = df["ticker"]
    df["value"] = df["close"]
    df["unit"] = df["currency"]
    df["category"] = df["sector"]
    df["source"] = "yahoo"
    # frequency already set by get_ticker_history ("D" or "5min"); fall back
    # to "D" if a caller hands to_common_schema a frame that predates that field.
    if "frequency" not in df.columns:
        df["frequency"] = "D"
    df["last_updated"] = datetime.now(UTC)
    df["is_forecast"] = False
    df["is_stale"] = False

    df = _clean(df)
    return validate(df, name="yahoo")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_RETRYABLE),
    reraise=True,
)
def _get_ticker_history_with_retry(ticker: str, range_: str, interval: str) -> pd.DataFrame:
    return get_ticker_history(ticker, range_=range_, interval=interval)


def _load_snapshot() -> pd.DataFrame | None:
    """Load the on-disk snapshot, or None if it doesn't exist yet."""
    if not SNAPSHOT_FILE.exists():
        return None
    try:
        return pd.read_csv(SNAPSHOT_FILE, parse_dates=["date", "last_updated"])
    except (OSError, ValueError, pd.errors.EmptyDataError) as exc:
        logger.warning("yahoo_client: failed to read snapshot %s: %s", SNAPSHOT_FILE, exc)
        return None


def _snapshot_rows_for(snapshot_df: pd.DataFrame | None, ticker: str) -> pd.DataFrame | None:
    if snapshot_df is None or snapshot_df.empty:
        return None
    rows = snapshot_df[snapshot_df["metric_id"] == ticker]
    return rows if not rows.empty else None


def _write_snapshot(df: pd.DataFrame) -> None:
    if df.empty:
        return
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(SNAPSHOT_FILE, index=False)


def fetch_market_data(
    tickers: list[str] | None = None,
    range_: str = "1mo",
    interval: str = "1d",
) -> pd.DataFrame:
    """Fetch history for multiple tickers into one common-schema DataFrame.

    Each ticker is retried independently (max 3 attempts, exponential
    backoff). A ticker that still fails falls back to its rows in the
    on-disk snapshot with ``is_stale=True``; if no snapshot rows exist for
    it, it is omitted. A ticker with a legitimately empty response (market
    closed / holiday / weekend) is neither an error nor stale - it's just
    absent from that fetch. If every ticker is unavailable this returns an
    empty DataFrame with all COMMON_COLUMNS rather than raising. Any
    newly-fetched (live, non-empty) rows overwrite the on-disk snapshot.
    """
    tickers = tickers or ALL_TICKERS
    snapshot_df = _load_snapshot()

    frames: list[pd.DataFrame] = []
    live_frames: list[pd.DataFrame] = []

    for ticker in tickers:
        try:
            raw = _get_ticker_history_with_retry(ticker, range_, interval)
            live_df = to_common_schema(raw)
            if not live_df.empty:
                frames.append(live_df)
                live_frames.append(live_df)
        except Exception as exc:  # noqa: BLE001 - one bad ticker shouldn't kill the batch
            logger.warning(
                "yahoo_client: live fetch failed for %s after retries: %s", ticker, exc
            )
            snap_rows = _snapshot_rows_for(snapshot_df, ticker)
            if snap_rows is not None:
                snap_rows = snap_rows.copy()
                snap_rows["is_stale"] = True
                frames.append(snap_rows)
            else:
                logger.warning(
                    "yahoo_client: no snapshot available for %s; omitting", ticker
                )

    if not frames:
        return pd.DataFrame(columns=COMMON_COLUMNS)

    combined = _clean(pd.concat(frames, ignore_index=True))

    if live_frames:
        _write_snapshot(_clean(pd.concat(live_frames, ignore_index=True)))

    return validate(combined, name="yahoo:fetch_market_data")


if __name__ == "__main__":
    df = fetch_market_data()
    print(df.head())
    print(df.tail())
    print(df.dtypes)
    print("rows:", len(df), "| tickers:", df["metric_id"].nunique())
