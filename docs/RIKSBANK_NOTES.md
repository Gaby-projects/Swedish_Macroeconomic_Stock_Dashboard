# Riksbank Monetary Policy Data API — notes

Verified findings only. Full acceptance-criteria detail lives here; a short
summary is cross-posted to `docs/api-research.md`.

## Schema changes (owned by riksbank-data-expert)

- **Phase 0 (done earlier):** added `is_stale` (bool, default `False`) as the
  tenth entry of `COMMON_COLUMNS` in `data/schema.py`. `validate()` raises
  `SchemaError` if it's missing. Both `riksbank_client.py` and
  `yahoo_client.py` set `is_stale = False` on their live-fetch success path.
- **This pass:** no further schema changes. `fetch_macro_series()` now also
  guarantees `is_stale = True` on every row that comes from the on-disk
  snapshot fallback (see below) — this was already representable by the
  Phase-0 column, no new column needed.
- **Date convention:** dates are kept exactly as returned by the API's `dt`
  field (already period-start dates: e.g. a Q1 observation is `YYYY-01-01`,
  a monthly observation is the first of its month). No re-normalisation is
  applied; this matches the "Quality assumptions" note in `SPEC.md` §2.1.

## Confirmed endpoint

- Base URL: `https://api.riksbank.se/monetary_policy_data/v1`
- `GET /forecasts/policy_rounds` → `{"data": [...]}` list of policy round
  names (e.g. `"2026:2"`); `max()` gives the latest round.
- `GET /forecasts?series={code}&policy_round_name={round}` → `{"data": [{...}]}`
  with one element per series requested. **No subscription key required** —
  confirmed live via direct HTTP calls during this session (2026-09-11). If
  this ever starts requiring auth, flag to pm-plan before adding secrets
  handling.
- The `policy_round_name` value contains a literal `:` that must **not** be
  percent-encoded (`requests` doing so causes a 404) — the client builds that
  query string manually rather than passing it as a `params=` dict.

## Response shape → schema mapping

Per series, `data[0]` has:

| Raw field | Common schema column |
|---|---|
| `external_id` | `metric_id` |
| `vintages.observations[].dt` | `date` |
| `vintages.observations[].value` | `value` |
| `metadata.unit` (fallback only) | `unit` (overridden by `RIKSBANK_SERIES[code]["unit"]`) |
| — (curated) | `category`, `frequency` from `RIKSBANK_SERIES[code]` |
| — (constant) | `source = "riksbank"` |
| fetch time | `last_updated` |
| `vintages.metadata.forecast_cutoff_date` compared to `date` | `is_forecast` |
| — (constant `False`, or `True` on snapshot fallback) | `is_stale` |

Extra columns kept for reference, not part of the contract: `policy_round`,
`forecast_cutoff_date`.

## Confirmed historical depth (job item 5)

Checked live against the latest policy round (`2026:2`) for two series
spanning different categories/frequencies:

| Series | Frequency | Earliest observation | Latest observation (incl. forecast) | Forecast cutoff |
|---|---|---|---|---|
| `SEMCPIFNAYNA` (CPIF) | M | 1988-01-31 | 2029-06-30 | 2026-05-31 |
| `SEQGDPNAYSA` (GDP) | Q | 1982-03-31 | 2029-06-30 | 2026-03-31 |

**Conclusion: a single policy-round vintage comfortably covers well beyond
10 years of actuals for both series checked (36+ and 40+ years respectively).
No multi-vintage stitching is needed.** The other 5 series are fetched
through the identical `/forecasts` mechanism and are assumed to have
comparable depth; this is an inference from the shared API design, not
individually re-measured — if a downstream tile ever shows a short span for
one of them, that's a signal to re-check that specific series here rather
than assume the client is broken.

No series is currently known to have under 10 years of actuals.

## Rate limits and retry/backoff tuning

No documented rate limits. Client uses `tenacity`, per series, independently:
`stop_after_attempt(3)`, `wait_exponential(multiplier=1, min=1, max=8)`,
retrying only on `requests.RequestException` (covers timeouts, connection
errors and raised HTTP status via `raise_for_status()`), `reraise=True` so
the caller sees the underlying exception type. One series exhausting retries
never stops the others from being fetched — each series has its own
try/except in `fetch_macro_series()`.

## Snapshot fallback

- Location: `<repo_root>/data_snapshots/riksbank_macro.csv` (CSV, not
  parquet — no `pyarrow` dependency is added by this client; format is
  recorded here for `yahoo-data-expert` to match if convenient, per
  `SPEC.md` §3.4's "both must use the same format").
- Written on every fetch where at least one series succeeded live (only the
  live rows are written, not stale carry-over rows).
- On a series' final failure, its rows are read back from the snapshot file
  (filtered by `metric_id`) and returned with `is_stale = True`; if the
  snapshot has no rows for that series, it is simply omitted from the
  result (never raised).
- If every series fails and no snapshot exists at all, `fetch_macro_series()`
  returns an empty DataFrame with all `COMMON_COLUMNS` and raises nothing.

## Cleaning applied before every return (live or snapshot path)

1. `schema.validate()` column-presence check.
2. Dedup on `(date, metric_id)`, keeping the last row.
3. Dtype coercion: `date`/`last_updated` → `datetime64[ns]`, `value` →
   `float64`, `is_forecast`/`is_stale` → `bool`.
4. Rows with a null `date`, `metric_id` or `value` are dropped; the dropped
   count is logged (not raised).
5. Sorted by `metric_id`, then `date`.

## Series-specific quirks

- All 7 series are quarterly (`Q`) or monthly (`M`); no daily/intraday macro
  data exists in this API.
- Observations run well past `forecast_cutoff_date` into the Riksbank's own
  forecast horizon (~2029 as of the `2026:2` round) — these rows are kept
  and flagged `is_forecast=True`, never dropped.
- Because each fetch pulls the *latest* policy round only, a series'
  forecast rows change every policy round (~5x/year); actual rows for
  already-passed periods can also revise slightly between rounds as
  Statistics Sweden updates outturns. This client does not track revisions
  across rounds (out of scope per `SPEC.md` non-goals).
- No gaps were observed in the two series spot-checked; gaps inside a
  series, if any, are left as-is (no interpolation), per SPEC.md.
