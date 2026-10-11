"""Client for the Riksbank Monetary Policy Data API.

No subscription key is required for these endpoints (verified live).
Docs: https://developer.api.riksbank.se/api-details#api=monetary-policy-data

Output conforms to data.schema.COMMON_COLUMNS.

Hardening on top of the basic fetch:
- Per-series retry with exponential backoff (tenacity), independent per
  series so one failing series never blocks the others.
- On-disk snapshot fallback: a successful fetch overwrites
  ``<repo_root>/data_snapshots/riksbank_macro.csv``; a series that still
  fails after retries falls back to its rows from that snapshot with
  ``is_stale=True``. If no snapshot row exists for a series it is simply
  omitted (never raised).
- Cleaning (dedup, dtype coercion, null handling, sort) applied before the
  final ``schema.validate()`` on every return path.
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

from swedish_macro_dashboard.constants import CORE_RIKSBANK_SERIES, RIKSBANK_SERIES
from swedish_macro_dashboard.data.schema import COMMON_COLUMNS, validate

BASE_URL = "https://api.riksbank.se/monetary_policy_data/v1"

# Snapshot lives at the repo root, not inside the installed package, so it's
# easy to gitignore and doesn't get bundled into the package.
SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "data_snapshots"
SNAPSHOT_FILE = SNAPSHOT_DIR / "riksbank_macro.csv"

logger = logging.getLogger(__name__)

_RETRYABLE = (requests.RequestException,)


def get_series_ids() -> pd.DataFrame:
    """Return metadata for all series available in the API (for discovery/debugging)."""
    resp = requests.get(f"{BASE_URL}/forecasts/series_ids", timeout=15)
    resp.raise_for_status()
    return pd.json_normalize(resp.json()["data"], sep="_")


def get_policy_rounds() -> list[str]:
    """Return all policy round names, e.g. '2026:2'."""
    resp = requests.get(f"{BASE_URL}/forecasts/policy_rounds", timeout=15)
    resp.raise_for_status()
    return resp.json()["data"]


def get_latest_policy_round() -> str:
    """Return the most recent policy round name."""
    return max(get_policy_rounds())


def get_series(series_id: str, policy_round_name: str | None = None) -> pd.DataFrame:
    """Fetch one series' observations for a given policy round vintage.

    If policy_round_name is None, uses the latest available round.
    A vintage's observations run years past its forecast_cutoff_date,
    so each row is flagged is_forecast to separate actual outcomes
    from the Riksbank's own forward-looking forecast.

    Returns a DataFrame conforming to schema.COMMON_COLUMNS, plus two
    extra columns (policy_round, forecast_cutoff_date) kept for reference.

    Raises requests.RequestException on network/HTTP failure; callers that
    want retry + snapshot fallback should go through fetch_macro_series().
    """
    if policy_round_name is None:
        policy_round_name = get_latest_policy_round()

    # NOTE: the API 404s if requests percent-encodes the ':' in policy_round_name
    # (e.g. "2026%3A2"). Build the query string manually to keep it literal.
    resp = requests.get(
        f"{BASE_URL}/forecasts?series={series_id}&policy_round_name={policy_round_name}",
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()["data"][0]

    vintage_meta = payload["vintages"]["metadata"]
    series_meta = RIKSBANK_SERIES.get(series_id, {})

    df = pd.DataFrame(payload["vintages"]["observations"]).rename(columns={"dt": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df["metric_id"] = payload["external_id"]
    df["unit"] = series_meta.get("unit", payload["metadata"]["unit"])
    df["category"] = series_meta.get("category", "Uncategorized")
    df["source"] = "riksbank"
    df["frequency"] = series_meta.get("frequency", "Q")
    df["last_updated"] = datetime.now(UTC)
    df["policy_round"] = vintage_meta["policy_round"]
    df["forecast_cutoff_date"] = pd.to_datetime(vintage_meta["forecast_cutoff_date"])
    df["is_forecast"] = df["date"] > df["forecast_cutoff_date"]
    df["is_stale"] = False

    return validate(df, name=f"riksbank:{series_id}")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_RETRYABLE),
    reraise=True,
)
def _get_series_with_retry(series_id: str, policy_round_name: str) -> pd.DataFrame:
    return get_series(series_id, policy_round_name=policy_round_name)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_RETRYABLE),
    reraise=True,
)
def _get_latest_policy_round_with_retry() -> str:
    return get_latest_policy_round()


def _load_snapshot() -> pd.DataFrame | None:
    """Load the on-disk snapshot, or None if it doesn't exist yet."""
    if not SNAPSHOT_FILE.exists():
        return None
    try:
        return pd.read_csv(SNAPSHOT_FILE, parse_dates=["date", "last_updated"])
    except (OSError, ValueError, pd.errors.EmptyDataError) as exc:
        logger.warning("riksbank_client: failed to read snapshot %s: %s", SNAPSHOT_FILE, exc)
        return None


def _snapshot_rows_for(snapshot_df: pd.DataFrame | None, series_id: str) -> pd.DataFrame | None:
    if snapshot_df is None or snapshot_df.empty:
        return None
    rows = snapshot_df[snapshot_df["metric_id"] == series_id]
    return rows if not rows.empty else None


def _write_snapshot(df: pd.DataFrame) -> None:
    if df.empty:
        return
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(SNAPSHOT_FILE, index=False)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the shared cleaning rules (docs/SPEC.md 3.5) before returning.

    Dedup on (date, metric_id) keeping the last row, dtype coercion, drop
    rows with a null date/metric_id/value (logging the count), sort by
    metric_id then date.
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
            "riksbank_client: dropped %d row(s) with null date/metric_id/value", dropped
        )

    df = df.drop_duplicates(subset=["date", "metric_id"], keep="last")
    df = df.sort_values(["metric_id", "date"]).reset_index(drop=True)
    return df


def fetch_macro_series(series_ids: list[str] | None = None) -> pd.DataFrame:
    """Fetch multiple series (same latest policy round) into one long DataFrame.

    Each series is retried independently (max 3 attempts, exponential
    backoff) on top of a single shared policy-round lookup. A series that
    still fails falls back to its rows in the on-disk snapshot with
    ``is_stale=True``; if no snapshot rows exist for it, it is omitted. If
    every series is unavailable this returns an empty DataFrame with all
    COMMON_COLUMNS rather than raising. Any newly-fetched (live) rows
    overwrite the on-disk snapshot.
    """
    series_ids = series_ids or CORE_RIKSBANK_SERIES

    try:
        round_name = _get_latest_policy_round_with_retry()
    except _RETRYABLE:
        round_name = None

    snapshot_df = _load_snapshot()

    frames: list[pd.DataFrame] = []
    live_frames: list[pd.DataFrame] = []

    for series_id in series_ids:
        try:
            if round_name is None:
                raise requests.RequestException("no policy round available")
            live_df = _get_series_with_retry(series_id, round_name)
            frames.append(live_df)
            live_frames.append(live_df)
        except _RETRYABLE as exc:
            logger.warning(
                "riksbank_client: live fetch failed for %s after retries: %s", series_id, exc
            )
            snap_rows = _snapshot_rows_for(snapshot_df, series_id)
            if snap_rows is not None:
                snap_rows = snap_rows.copy()
                snap_rows["is_stale"] = True
                frames.append(snap_rows)
            else:
                logger.warning(
                    "riksbank_client: no snapshot available for %s; omitting", series_id
                )

    if not frames:
        return pd.DataFrame(columns=COMMON_COLUMNS)

    combined = _clean(pd.concat(frames, ignore_index=True))

    if live_frames:
        _write_snapshot(_clean(pd.concat(live_frames, ignore_index=True)))

    return validate(combined, name="riksbank:fetch_macro_series")


if __name__ == "__main__":
    df = fetch_macro_series()
    print(df.head())
    print(df.tail())
    print(df.dtypes)
    print("rows:", len(df))
