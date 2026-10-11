"""Shared data contract that every data source (Riksbank, Yahoo) must conform to.

This is what lets tabs/ and transforms/ code stay source-agnostic: it only
ever needs to know about these columns, never about riksbank_client.py or
yahoo_client.py directly.
"""

from __future__ import annotations

import pandas as pd

COMMON_COLUMNS = [
    "date",          # datetime64
    "metric_id",     # Riksbank series code or ticker symbol
    "value",         # float - the observation (macro value, or stock close price)
    "unit",          # e.g. "% y/y", "Per cent", "SEK"
    "category",      # macro: Growth/Labour/Prices/Policy; market: sector name
    "source",         # "riksbank" or "yahoo"
    "frequency",       # "D"/"M"/"Q"
    "last_updated",     # UTC timestamp of when this row was fetched
    "is_forecast",       # True if this row is a forward-looking forecast, not an actual outcome
    "is_stale",          # bool, default False; True only when a client returns an on-disk snapshot after a failed live fetch
]


class SchemaError(Exception):
    pass


def validate(df: pd.DataFrame, name: str = "dataframe") -> pd.DataFrame:
    """Raise SchemaError if df is missing any required column. Extra columns are allowed."""
    missing = set(COMMON_COLUMNS) - set(df.columns)
    if missing:
        raise SchemaError(f"{name} is missing required columns: {sorted(missing)}")
    return df
