"""Transforms over a cleaned Riksbank macro DataFrame (COMMON_COLUMNS long format).

Every function here takes an already-validated-and-cleaned ``macro_df`` (see
``data/schema.py`` and ``data/riksbank_client.py`` / ``data/fixtures.py``) and
never re-validates or re-cleans it. Downstream tabs consume these, not the raw
client output.
"""

from __future__ import annotations

import pandas as pd

# Riksbank's inflation target, used for the CPIF gap calculations.
CPIF_TARGET = 2.0

# Fixed label set for cycle_state(); at most 5 values, per docs/SPEC.md acceptance
# criteria.
CYCLE_LABELS = ("Expansion", "Recovery", "Slowdown", "Contraction", "Neutral")

GDP_GAP_SERIES = "SEQGDPGAPYSA"
CPIF_SERIES = "SEMCPIFNAYNA"
CPIF_EX_ENERGY_SERIES = "SEMCPIFFEXYNA"
POLICY_RATE_SERIES = "SEQRATENAYNA"


def latest_actuals(macro_df: pd.DataFrame) -> pd.DataFrame:
    """One row per ``metric_id``: its most recent non-forecast observation."""
    actuals = macro_df[~macro_df["is_forecast"]]
    if actuals.empty:
        return actuals.reset_index(drop=True)
    idx = actuals.groupby("metric_id")["date"].idxmax()
    return actuals.loc[idx].sort_values("metric_id").reset_index(drop=True)


def sparkline_series(
    macro_df: pd.DataFrame, metric_id: str, include_forecast: bool = False
) -> pd.Series:
    """Date-indexed ``value`` series for one metric, for a tile sparkline.

    Excludes forecast rows unless ``include_forecast`` is True, in which case
    the forecast tail is still distinguishable via the caller re-joining on
    ``is_forecast`` if needed for a dashed segment.
    """
    subset = macro_df[macro_df["metric_id"] == metric_id]
    if not include_forecast:
        subset = subset[~subset["is_forecast"]]
    subset = subset.sort_values("date")
    return subset.set_index("date")["value"]


def period_over_period_change(macro_df: pd.DataFrame, metric_id: str) -> float | None:
    """Change between the latest and second-latest non-forecast observation.

    Returns None if fewer than two actual observations exist for the metric.
    """
    series = sparkline_series(macro_df, metric_id, include_forecast=False)
    if len(series) < 2:
        return None
    return float(series.iloc[-1] - series.iloc[-2])


def _latest_value(macro_df: pd.DataFrame, metric_id: str) -> float | None:
    series = sparkline_series(macro_df, metric_id, include_forecast=False)
    if series.empty:
        return None
    return float(series.iloc[-1])


def gap_metrics(macro_df: pd.DataFrame) -> dict[str, float | None]:
    """GDP gap and CPIF (incl./excl. energy) gaps vs. the 2% target.

    Returns a dict with keys ``gdp_gap``, ``cpif_gap``, ``cpif_ex_energy_gap``.
    A key is None if the underlying series has no actual observations.
    """
    gdp_gap = _latest_value(macro_df, GDP_GAP_SERIES)
    cpif = _latest_value(macro_df, CPIF_SERIES)
    cpif_ex_energy = _latest_value(macro_df, CPIF_EX_ENERGY_SERIES)
    return {
        "gdp_gap": gdp_gap,
        "cpif_gap": None if cpif is None else cpif - CPIF_TARGET,
        "cpif_ex_energy_gap": (
            None if cpif_ex_energy is None else cpif_ex_energy - CPIF_TARGET
        ),
    }


def cycle_state(macro_df: pd.DataFrame) -> tuple[str, str]:
    """Composite "state of the cycle" label plus a one-line rationale.

    Combines the GDP gap, the CPIF gap (vs. the 2% target) and the current
    policy rate into one label from ``CYCLE_LABELS``. Deterministic given
    ``macro_df``.
    """
    gaps = gap_metrics(macro_df)
    gdp_gap = gaps["gdp_gap"]
    cpif_gap = gaps["cpif_gap"]
    policy_rate = _latest_value(macro_df, POLICY_RATE_SERIES)

    if gdp_gap is None or cpif_gap is None:
        return "Neutral", "Insufficient data to classify the cycle."

    if gdp_gap >= 0 and cpif_gap >= 0:
        label = "Expansion"
        rationale = "Output is above trend and inflation is at or above target."
    elif gdp_gap >= 0 and cpif_gap < 0:
        label = "Recovery"
        rationale = "Output is above trend but inflation is still below target."
    elif gdp_gap < 0 and cpif_gap >= 0:
        label = "Slowdown"
        rationale = "Output is below trend while inflation stays at or above target."
    else:
        label = "Contraction"
        rationale = "Output is below trend and inflation is below target."

    if policy_rate is not None:
        rationale += f" Policy rate is {policy_rate:.2f}%."

    return label, rationale
