# API research

Shared between `riksbank-data-expert` and `yahoo-data-expert`. Each adds its
own section below and never overwrites the other's. Full detail belongs in
each agent's own `*_NOTES.md`; this file is a brief cross-reference (linked
from `constants.py`'s docstring).

## Auth

Confirmed live, 2026-09-11: neither the Riksbank Monetary Policy Data API
nor the Yahoo Finance chart endpoint requires a subscription key, API key or
token. Both return 200 with a plain unauthenticated `requests.get()`.

## Riksbank

- Base URL: `https://api.riksbank.se/monetary_policy_data/v1`. No
  subscription key required (see Auth above).
- All 7 series in `constants.RIKSBANK_SERIES` are fetched via
  `GET /forecasts?series={code}&policy_round_name={round}`, one HTTP call
  per series, same policy round across a batch. Latest round comes from
  `GET /forecasts/policy_rounds` (`max()` of the returned names, e.g.
  `"2026:2"`).
- Response → schema mapping, cleaning rules and historical-depth
  confirmation: see `RIKSBANK_NOTES.md` (full detail lives there).
- Historical depth: a single vintage reaches back to 1988 (CPIF, monthly)
  and 1982 (GDP, quarterly) as of the `2026:2` round — well beyond the 10y
  target. No multi-vintage stitching needed.
- Snapshot fallback format: **CSV**, at `<repo_root>/data_snapshots/riksbank_macro.csv`.

## Yahoo Finance

- Endpoint: `GET https://query1.finance.yahoo.com/v8/finance/chart/{ticker}`,
  called directly via `requests` (no auth - see Auth above), one HTTP call
  per ticker, `params={"range": ..., "interval": ...}`.
- Why not `yfinance`: on this dev machine, `yfinance`'s `curl_cffi` HTTP
  backend fails SSL verification against a locally-trusted root CA (a
  machine-level cert-store issue, not an API problem). Calling the chart
  endpoint directly with `requests` (which respects the system cert store
  via `pip-system-certs`) sidesteps that dependency entirely. Do not switch
  back without independently re-verifying the SSL issue is gone on the
  target machine.

### Response shape -> `COMMON_COLUMNS` mapping

`chart.result[0]` has:

| Raw field | Common schema column |
|---|---|
| `meta.currency` | `unit` (always `"SEK"` for these 19 tickers, confirmed) |
| — (from `constants.SECTOR_BY_TICKER[ticker]`) | `category` |
| `timestamp[]` (Unix seconds, UTC) -> converted to `Europe/Stockholm`, tz stripped | `date` |
| `indicators.quote[0].close[]` | `value` |
| — (constant) | `source = "yahoo"` |
| — (derived from `interval`: daily-or-coarser -> `"D"`, else -> `"5min"`) | `frequency` |
| fetch time | `last_updated` |
| — (constant `False`; market rows are never forecasts) | `is_forecast` |
| — (constant `False`, or `True` on snapshot fallback) | `is_stale` |

`open`/`high`/`low`/`volume` and `currency` are kept on the intermediate
OHLCV frame (`get_ticker_history()`) for richer charts but are not part of
`COMMON_COLUMNS`.

### Intraday granularity ("last 24h" / last-session chart)

- `range=1d&interval=5m` returns 5-minute bars for the **current session
  only**; `meta.hasPrePostMarketData` is `false` (no pre/post-market bars) -
  originally spot-checked on `VOLV-B.ST`, and re-confirmed in this pass on
  `ERIC-B.ST` (Telecom/Tech sector): 57 five-minute bars returned,
  `meta.currency = "SEK"`, `meta.exchangeTimezoneName = "Europe/Stockholm"`,
  no `chart.error`. Bar count varies with how far into the session the
  request lands (a full 09:00-17:30 CET session is ~102 bars; fewer bars
  simply means the session is still in progress or the market is not yet
  fully open) - this is expected and not a data-quality issue.
- **Known bug, fixed in `yahoo_client.get_ticker_history`:** the client
  used to call `.normalize()` on every timestamp unconditionally, which
  collapsed all of a session's 5-minute bars onto a single midnight
  timestamp. It now normalizes only when `interval` is daily-or-coarser
  (`1d`, `5d`, `1wk`, `1mo`, `3mo`, ...); intraday intervals keep their full
  timestamp and get `frequency="5min"`.
- **Market-closed / weekend / holiday behavior:** when there is no
  current-session data yet, Yahoo's chart endpoint has two observed shapes
  depending on the ticker/timing: (a) `chart.result[0].timestamp` is `null`
  (or the key is simply absent) with an empty `indicators.quote[0]`, or (b)
  `chart.result` itself is an empty list. `get_ticker_history()` treats
  both as "no data for this window" and returns an empty (but
  correctly-shaped) OHLCV frame rather than raising - this is a
  legitimate empty result, not an error, and `fetch_market_data()` does
  **not** mark it stale or fall back to a snapshot for it (only an actual
  fetch failure after retries does that). This was not independently
  re-verified against an actual live closed-market timestamp in this pass
  (only inferred from the documented `null`-timestamp shape and defensive
  coding); if a future session observes a different shape, update this
  note and the `get_ticker_history` handling together.

### Daily history depth (`range=10y&interval=1d`)

- Confirmed live on `SEB-A.ST` (Banking sector): `meta.currency = "SEK"`,
  `meta.instrumentType = "EQUITY"`, ~1,900 daily timestamp entries spanning
  multiple years, comfortably covering the "last 1y" and "last 3-5y"
  lookback windows the Stock Market tab needs. One `null` close was present
  near the end of the series in this spot-check, confirming the need for
  the null-drop step in cleaning (a halted/partial bar, not a client bug).
  The exact upper-bound date returned by `range=10y` was not independently
  re-verified to the day beyond this single spot-check; treat "multi-year,
  comfortably >1y" as the confirmed fact rather than an exact day count.

### Rate limits / throttling across the 19-ticker batch

- No documented rate limit. `fetch_market_data()` makes 19 sequential
  per-ticker calls; no 429s were observed in ad-hoc spot-checks across
  sectors in this session. Per-ticker retry/backoff tuning mirrors
  `riksbank_client.py`: `tenacity`, `stop_after_attempt(3)`,
  `wait_exponential(multiplier=1, min=1, max=8)`, retrying only on
  `requests.RequestException` (timeouts, connection errors, and
  `raise_for_status()` HTTP errors), `reraise=True`. Each ticker has its
  own independent retry budget and `try/except` in `fetch_market_data()`,
  so one ticker exhausting retries never blocks or delays the other 18.

### Per-ticker quirks

- All 19 tickers checked so far (across Industrials, Telecom/Tech, and
  Banking sectors in live spot-checks) return `meta.currency = "SEK"` -
  no currency-mismatch cases observed; `unit` is hardcoded to `"SEK"` in
  the schema mapping regardless, per `SPEC.md` §2.2, so a mismatch would
  need to be caught by comparing against `meta.currency` if this ever
  changes.
- Null bars appear inside `indicators.quote[0].close[]` on partial/halted
  trading days (observed on the `SEB-A.ST` 10y daily pull) - these are
  dropped by `to_common_schema()`'s cleaning step (null `value` rows are
  removed), never passed through as zero or interpolated.
- No delistings or sparse-history tickers were observed among the 19 in
  scope during spot-checks; if one is found later, it surfaces as either
  an empty `chart.result` (handled as "no data") or a `chart.error`
  payload (raised as `ValueError` and caught per-ticker in
  `fetch_market_data()`, falling back to snapshot/omission like any other
  failure).
- Snapshot fallback format: **CSV**, at
  `<repo_root>/data_snapshots/yahoo_market.csv` - matches the format
  `riksbank-data-expert` chose for `riksbank_macro.csv` (SPEC.md §3.4).
