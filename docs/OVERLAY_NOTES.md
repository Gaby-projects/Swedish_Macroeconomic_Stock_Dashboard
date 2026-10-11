# OVERLAY_NOTES.md — Macro x Market overlay (F3 / CHART_SPECS.md C7-C8)

Owner: `macro-market-expert`. Consumed by `streamlit-builder`'s `tabs/macro_market.py` and by
`CHART_SPECS.md` C7/C8. Implementation: `src/swedish_macro_dashboard/transforms/overlay_transforms.py`.

## 1. Alignment strategy: forward-fill macro onto the market calendar

**Chosen:** the market calendar (daily) is the join target. For every market date, the overlay
attaches the most recent macro observation at or before that date — i.e. the macro series is
forward-filled across each daily/weekly gap until its next print. Implemented with
`pd.merge_asof(market_frame, macro_frame, on="date", direction="backward")` (confirmed against
current pandas docs as the idiomatic "as-of" join for this exact case, rather than hand-rolling
`reindex().ffill()`).

**Rejected alternative:** downsampling the market series to the macro frequency (e.g. one
month-end close per CPIF print). This was rejected because:
- It throws away the daily granularity `CHART_SPECS.md` C7 explicitly wants on the market axis and
  in C4/C5's daily charts elsewhere in the app — the market side of this tab should look like the
  same market data used everywhere else, not a resampled derivative.
- It shrinks the correlation sample size drastically (a 5-year overlay would have ~20 quarterly
  points instead of ~1,250 daily points), making C8's correlation number noisy and unstable across
  window changes.
- `CHART_SPECS.md` C7 already pins the x-axis to "date (market calendar)", which only makes sense
  under the forward-fill choice.

**Tradeoff, stated plainly:** forward-filling a quarterly or monthly print across the whole
following quarter/month overstates its apparent daily-level precision — the chart visually implies
the policy rate or CPIF "moved" or was "known" continuously, when in reality it was one point-in-time
print held constant until the next one. This is a standard and accepted convention for macro/market
overlays (it's what a Bloomberg or FRED overlay chart does), but it is a real distortion, not a
neutral default, and streamlit-builder's caption for C7/C8 should say so (copy is viz-ux-designer's
call; the caveat exists here so it isn't silently lost).

Forecast rows are always excluded before alignment (`macro_transforms.sparkline_series(...,
include_forecast=False)`), per `CHART_SPECS.md` C7 ("no dashed segment here, unlike C1").

## 2. Market-side representation: ticker vs. sector index

Both cases resolve to the exact same shape before alignment: a date-indexed `pd.Series` of floats.
`align_series()` (the generic primitive) never knows or cares which one it got.

- **Sector index:** `resolve_market_series(market_df, sector_name)` calls
  `market_transforms.equal_weighted_sector_index(market_df, sector_name)` directly — no
  reimplementation. That series is already rebased to 100 at its first date, per its own contract
  (T015). The overlay does **not** un-rebase it; the right axis for a sector selection is therefore
  always "index level, 100 = first available date", not SEK.
- **Single ticker:** `resolve_market_series(market_df, ticker)` returns the raw daily close price in
  SEK (`frequency == "D"`, `is_forecast == False` rows only — the same filter convention
  `market_transforms` uses internally, applied here only because there is no public
  single-raw-ticker-series accessor to call instead of duplicating a one-line filter). The right axis
  for a ticker selection is therefore in SEK, unrebased.

This asymmetry (index level vs. raw SEK price) is intentional and matches `CHART_SPECS.md` C7's own
axis-title rule ("the ticker symbol or `{Sector} index (equal-weighted)`") — the two market-selector
options are genuinely different units, which is exactly why C7 uses independent dual y-axes rather
than one shared scale.

## 3. Normalization helper (not used by C7, provided for future use)

`rebase_pair_to_100(aligned_df)` rebases `macro_value` and `market_value` to 100 at the first
non-null observation of each, adding `macro_rebased`/`market_rebased` columns. **C7 does not call
this** — per `CHART_SPECS.md` C7, the dual-axis chart deliberately keeps each series on its own
native-unit axis ("a policy-rate-vs-price-level comparison is more legible on independent scales").
It is exposed because a normalized/rebased-to-100 single-axis alternate view was explicitly
requested as a generic capability; if/when a chart wants it, that's a new chart per C7's own note
("flag to pm-plan rather than inventing it here"), not a silent change to C7.

## 4. The "so what" stat: correlation

`rolling_correlation(aligned_df, window)` — Pearson correlation coefficient
(`pandas.Series.corr`, default method) between `macro_value` and `market_value`, computed over the
rows in the trailing `window` only, using pairwise-complete rows (rows where either value is `NaN` —
i.e. before the macro series' first observation — are dropped first).

- **Window definition:** `window` is one of `"1y"`, `"3y"`, `"5y"`, `"max"` — the same options as
  C7's window selector, so the two never disagree (per C8's update rule). `"1y"`/`"3y"`/`"5y"` are
  365/1095/1825 **calendar** days back from the aligned frame's own latest market date (not "today"
  — the frame may lag today by up to one market-closed day). `"max"` uses the whole aligned frame.
  This is a single scalar for the selected window, not a rolling (time-varying) correlation series —
  "rolling" in the C8 copy ("Rolling correlation (1y): 0.42") describes the trailing window, not a
  windowed time series of correlation values. Streamlit-builder is not expected to plot a
  correlation *line*; C8 is a single `st.metric` value.
- **Readout:** `correlation_readout(aligned_df, window)` returns the exact string
  `"Rolling correlation ({window}): {corr:.2f}"`, or the exact string
  `"n/a — insufficient overlapping data"` (exported as `INSUFFICIENT_DATA_MESSAGE`) when fewer than
  2 overlapping (non-null-pair) points exist in the window — matching `CHART_SPECS.md` C8's
  missing-data rule verbatim. Never returns `NaN` or lets a Python exception propagate.

## 5. Known limitations

- **Short overlap window:** a market series only has as much history as the Yahoo fixture/client
  returns (fixtures: ~1 trading year; live client: whatever `range=10y` actually returns, per
  `docs/api-research.md`). If that history is shorter than the macro series' full span, the "5y" or
  "max" window is effectively capped by the market side, not the macro side, since the market
  calendar is the join target. This is expected, not a bug — it's the direct consequence of choosing
  the market calendar as the alignment target (§1).
- **Forward-fill during macro gaps:** if a macro series has an internal gap (a missed print — SPEC.md
  2.1 says gaps are left as gaps, never interpolated), the forward-fill carries the last real value
  across that gap too, indistinguishable from an on-time print. There is no separate "stale due to
  gap" flag; only `is_stale` (snapshot-fallback) is surfaced via `macro_is_stale`/`market_is_stale`
  in `overlay_frame()`.
- **Sector-index staleness is coarse:** `market_is_stale` for a sector selection is `True` if *any*
  constituent ticker contributed a stale row in the window, even if the other constituents were
  live. `equal_weighted_sector_index()` itself doesn't propagate per-row staleness, so this is the
  most precise signal available without changing that function's contract (out of scope here).
- **Calendar-day, not trading-day, windows:** `"1y"/"3y"/"5y"` are calendar-day offsets for
  simplicity and consistency with how a human reads "1 year back", not exact trading-day counts —
  unlike `market_transforms`'s `returns_table()`, which intentionally uses trading-day windows for
  return calculations. The two are not meant to produce identical row counts for "1y".

## 6. Functions exposed (`overlay_transforms.py`)

| Function | Purpose |
|---|---|
| `align_series(macro, market)` | Generic primitive: align any two date-indexed value `pd.Series`. |
| `align_macro_market(macro_df, market_series, metric_id)` | `align_series` with the macro leg resolved from `macro_df` by `metric_id` (forecast rows excluded). |
| `resolve_market_series(market_df, ticker_or_sector)` | Resolve a ticker *or* sector name to a date-indexed value `pd.Series`. |
| `overlay_frame(macro_df, market_df, macro_metric_id, market_series_id, window)` | The exact frame C7 consumes: aligned + windowed + labelled (`macro_unit`, `macro_description`, `market_label`) + staleness flags. |
| `rebase_pair_to_100(aligned_df)` | Normalization helper (§3); not used by C7. |
| `rolling_correlation(aligned_df, window)` | C8's scalar stat. |
| `correlation_readout(aligned_df, window)` | C8's plain-language string (or the pinned "insufficient data" message). |
