# SPEC.md — Swedish Macro + Stock Market Dashboard (MVP)

Ground truth for layout, run command, data-source facts, ownership and execution order lives in the repo-root `CLAUDE.md`. This spec adds the product contract on top of it; it does not restate it.

---

## 1. Objective

A local Streamlit dashboard that puts Sweden's macro cycle (Riksbank Monetary Policy Data API) next to the Stockholm equity market (Yahoo Finance chart API) in one place. It is both a portfolio piece demonstrating a multi-source, schema-driven data app and a genuinely usable tool for a Sweden-focused analyst.

**Primary audience:** an individual macro/equity analyst or student following Sweden who wants a single screen answering "where are we in the cycle, and how is the market positioned against it?"

**Decision it enables:** quick read of cycle state (growth, labour, prices, policy) and sector-level market performance, plus a visual check of whether a chosen market series is moving with or against a chosen macro series — without opening two data portals.

**Definition of done for the MVP:** `uv sync` then `uv run streamlit run app.py` boots a three-tab dashboard (Macro Overview, Stock Market, Macro x Market) with a sidebar refresh button and per-source staleness badges, and `uv run pytest` passes with mocked HTTP.

---

## 2. Data Assumptions

### 2.1 Riksbank Monetary Policy Data API

- **Base URL / auth:** see `CLAUDE.md`. No subscription key required (verified live in `riksbank_client.py`). If this changes, riksbank-data-expert flags it to pm-plan; do not add secrets handling speculatively.
- **In-scope series (exactly 7, from `constants.RIKSBANK_SERIES`):**

  | Code | Description | Unit | Category | Freq |
  |---|---|---|---|---|
  | `SEQGDPNAYSA` | GDP | Annual % change | Growth | Q |
  | `SEQGDPGAPYSA` | GDP gap | Per cent | Growth | Q |
  | `SEQLABUEASA` | Unemployment rate | % of labour force | Labour | Q |
  | `SEMCPIFNAYNA` | CPIF | Annual % change | Prices | M |
  | `SEMCPIFFEXYNA` | CPIF excl. energy | Annual % change | Prices | M |
  | `SEQRATENAYNA` | Policy rate | Per cent | Policy | Q |
  | `SEDMENAYNA` | Policy rate expectations | Per cent | Policy | Q |

- **Response shape:** series are fetched per policy-round vintage; each observation carries a date, a value and an `is_forecast` flag. The current client fetches the latest policy round only.
- **Historical depth:** target is 10 years of actuals per series. **Unconfirmed** whether a single vintage reaches that far back. riksbank-data-expert must measure the earliest actual date per series and record it in `RIKSBANK_NOTES.md`. If any series has less than 10 years, the sparkline shows whatever is available and the tile label states the actual span — no synthetic backfill.
- **Rate limits:** none documented. Assume polite use: sequential per-series calls, retry with exponential backoff on 429/5xx/timeouts, max 3 attempts.
- **Refresh cadence:** macro data changes only at policy rounds (roughly 5 per year). App-level TTL: 6 hours. Refresh-now bypasses it.
- **Quality assumptions:** quarterly series dated to quarter start or end (riksbank-data-expert normalises to a consistent convention and documents it); monthly series dated to month start; forecast rows are kept but flagged `is_forecast=True` and excluded from "latest actual" tiles; gaps inside a series are left as gaps (no interpolation).

### 2.2 Yahoo Finance chart API

- **Endpoint / auth:** the chart endpoint called directly via `requests` — not the `yfinance` package (see `CLAUDE.md` and `yahoo-data-expert.md` for why). No auth.
- **In-scope tickers:** the 19 `.ST` tickers across 8 sectors in `constants.TICKERS_BY_SECTOR`. Sector membership is fixed by `SECTOR_BY_TICKER`; no agent adds or reassigns tickers.
- **Per-ticker response shape:** one JSON document per ticker with a `timestamp` array (Unix seconds) and parallel `indicators.quote[0]` arrays (`open`, `high`, `low`, `close`, `volume`). Nulls appear inside these arrays on partial bars. `meta` carries currency, exchange timezone and `regularMarketPrice`.
- **Intraday:** `range=1d&interval=5m` returns 5-minute bars for the current session only (verified). Daily history: `range=10y&interval=1d` is the assumed call for the long view; yahoo-data-expert confirms the actual depth returned and records it in `docs/api-research.md`.
- **Known bug to fix:** `get_ticker_history` currently calls `.normalize()` on timestamps, which collapses 5-minute bars into a single day. Intraday rows must keep full timestamps; daily rows may be normalised.
- **Rate limits:** undocumented; 19 sequential calls per refresh is acceptable. Per-ticker retry with backoff (max 3 attempts). One ticker failing must not fail the batch.
- **Refresh cadence:** market data changes continuously during the Stockholm session (09:00–17:30 CET/CEST). App-level TTL: 15 minutes for daily history and intraday. Refresh-now bypasses it.
- **Quality assumptions:** Swedish market holidays and weekends produce no rows (not zero rows); all-NaN ticker-days (e.g. halted trading) are dropped, not passed through; duplicate timestamps from overlapping range requests are deduplicated; prices are in SEK; `value` is the close price.

### 2.3 Cross-source

- Both sources are fetched independently; a failure in one never blocks the other tab.
- Macro is quarterly/monthly, market is daily/intraday. Alignment for overlays is done in `overlay_transforms.py` (forward-fill macro onto the market calendar, or resample market to macro frequency — macro-market-expert chooses and documents in `OVERLAY_NOTES.md`).

---

## 3. Shared Data Contract

### 3.1 Schema

The contract is `COMMON_COLUMNS` in `src/swedish_macro_dashboard/data/schema.py`. Long format: one row per `(date, metric_id)`.

| Column | Type | Meaning |
|---|---|---|
| `date` | `datetime64[ns]`, tz-naive UTC | Observation timestamp. Daily/monthly/quarterly rows at midnight; intraday rows keep full time. |
| `metric_id` | `str` | Riksbank series code (e.g. `SEMCPIFNAYNA`) or Yahoo ticker (e.g. `VOLV-B.ST`). |
| `value` | `float64` | The observation: macro value, or stock close price in SEK. Never null in returned data. |
| `unit` | `str` | From `RIKSBANK_SERIES[code]["unit"]` for macro; `"SEK"` for market. |
| `category` | `str` | Macro: `Growth` / `Labour` / `Prices` / `Policy` from `RIKSBANK_SERIES`. Market: sector name from `SECTOR_BY_TICKER`. |
| `source` | `str` | Exactly `"riksbank"` or `"yahoo"`. |
| `frequency` | `str` | `"Q"` / `"M"` for macro (from `RIKSBANK_SERIES`); `"D"` for daily market bars; `"5min"` for intraday bars. |
| `last_updated` | `datetime64[ns]`, tz-naive UTC | When this row was fetched (live) or when the snapshot it came from was written (stale). |
| `is_forecast` | `bool` | `True` for Riksbank forward-looking rows. Always `False` for market rows. |
| `is_stale` | `bool` | **To be added in Phase 0.** Default `False`. `True` only when the row comes from an on-disk snapshot returned after a failed live fetch. |

Extra columns are allowed (e.g. `description` for display names) but nothing downstream may depend on a column not in this table.

### 3.2 Ownership

- pm-plan decides the contract (this section).
- **riksbank-data-expert is the only agent that edits `schema.py`.** Every change is recorded in `RIKSBANK_NOTES.md`. After any change, yahoo-data-expert re-validates `yahoo_client.py` output against the updated schema.
- The `is_stale` addition is Phase 0 (task T003). Nothing in the parallel phase starts until it lands and both existing clients set it.

### 3.3 `metric_id` and metadata resolution

- Macro `metric_id` = Riksbank series code; descriptive metadata (description, unit, category, frequency) is resolved from `constants.RIKSBANK_SERIES`.
- Market `metric_id` = Yahoo ticker symbol; sector from `constants.SECTOR_BY_TICKER`; unit is `"SEK"`; display name is the ticker itself (no company-name lookup in MVP).
- `source` distinguishes the origin and is the key the sidebar uses to show one staleness badge per source.

### 3.4 `is_stale` semantics

- Set by the source client, never by transforms or UI.
- Live fetch succeeds -> all rows `is_stale=False`, and the client overwrites the on-disk snapshot for that source.
- Live fetch fails after retries -> client loads the snapshot for that source (or that series/ticker, if per-item snapshots are used) and returns it with `is_stale=True` and `last_updated` = snapshot write time.
- No snapshot and live fetch fails -> client returns an empty DataFrame with all `COMMON_COLUMNS` and raises no exception; the UI shows an "unavailable" state for that source.
- Snapshot location: `data/snapshots/` under the package (gitignored), one parquet or CSV per source. Format choice is the data agents'; both must use the same format and record it in `docs/api-research.md`.

### 3.5 Validation and cleaning ownership

Each source client validates and cleans its own output before returning:

- `schema.validate()` passes.
- Deduplicated on `(date, metric_id)`; keep the last occurrence.
- Dtypes coerced as in 3.1 (`date`/`last_updated` to datetime64, `value` to float64, flags to bool).
- Rows with null `date`, `metric_id` or `value` are dropped (and the count logged), never returned.
- Sorted by `metric_id`, then `date`.

Downstream layers (`transforms/`, `tabs/`, `app.py`) assume clean input and do not re-clean. Fixtures in `data/fixtures.py` must satisfy the same rules so UI work can proceed against them.

### 3.6 Consequence of violating the contract

A client whose output fails `schema.validate()` or the cleaning rules blocks every task that depends on it (overlay transforms, its tab, the test suite). The verifier reports the violation to the owning agent; no downstream agent works around it by re-cleaning or redefining columns.

---

## 4. MVP Feature List

### F1. Macro Overview tab

- **Description:** Tiles grouped by category (Growth, Labour, Prices, Policy). Each tile shows the latest *actual* value (excluding forecast rows), the observation date, and a sparkline of the available history (target 10y). A "State of the cycle" summary tile at the top shows a composite label (e.g. Expansion / Slowdown / Contraction / Recovery) and a short one-line rationale derived from `macro_transforms.cycle_state()`.
- **Inputs:** cleaned macro DataFrame (via `st.cache_data`-wrapped `fetch_macro_series`); `RIKSBANK_SERIES` metadata; a "show forecasts" toggle (default off) that overlays forecast rows on sparklines as a dashed segment.
- **Outputs:** 7 tiles in 4 category groups plus 1 summary tile; each tile labels its actual data span; stale data shows the sidebar badge and a muted tile caption.

### F2. Stock Market tab

- **Description:** One sub-tab per sector (8) plus an "All sectors" sub-tab. Each sector sub-tab shows a performance table (latest close, 1d, 1w, 1m, YTD, 1y % returns per ticker) and a chart with the sector's tickers rebased to 100 plus the equal-weighted sector index line. The "All sectors" sub-tab shows one row per sector with the equal-weighted index returns and a chart of all 8 sector indices rebased to 100.
- **Inputs:** cleaned market DataFrame (via `st.cache_data`-wrapped `fetch_market_data`); `market_transforms.returns_table()` and `equal_weighted_sector_index(market_df, sector)`; a lookback selector (1m / 6m / 1y / 5y / max) for charts.
- **Outputs:** table + chart per sub-tab; switching sub-tabs or lookback updates both. Tickers with no data in the window are listed with "n/a" rather than omitted.

### F3. Macro x Market tab

- **Description:** Overlay of one macro series against one market series (a ticker or a sector index) on a shared date axis with dual y-axes, plus a summary line of the correlation over the selected window. Macro is aligned to the market calendar as documented in `OVERLAY_NOTES.md`.
- **Inputs:** both cleaned DataFrames; a macro-series selector (7 options), a market-series selector (19 tickers + 8 sector indices), a window selector (1y / 3y / 5y / max); `overlay_transforms.align_macro_market()` and `overlay_transforms.rolling_correlation()` (names final once macro-market-expert lands them).
- **Outputs:** one dual-axis chart, one correlation number with its window, and a caption stating the alignment method. Forecast rows are excluded from the overlay.

### F4. Sidebar controls and freshness indicators

- **Description:** A "Refresh now" button that clears all `st.cache_data` entries and reruns. Two freshness badges (Riksbank, Yahoo) showing `last_updated` (max over rows, as local time) and a colour state: green = live, amber = stale snapshot (`is_stale` true for any row), red = unavailable (empty frame). A "Fixtures mode" toggle (default off) that swaps both sources for `fixtures.py` data so the UI runs with no network.
- **Inputs:** both DataFrames; `is_stale` and `last_updated` columns.
- **Outputs:** button, two badges, one toggle; badges update on every rerun.

### F5. Caching layer

- **Description:** No standalone cache module. Two mechanisms: (a) `st.cache_data(ttl=...)` wrappers in `app.py` around each client call — macro TTL 6h, market TTL 15min — cleared by Refresh-now; (b) on-disk snapshot fallback inside each client, returning `is_stale=True` after a failed live fetch. The snapshot is written on every successful live fetch.
- **Inputs:** client return values; TTL constants; snapshot files.
- **Outputs:** repeated reruns inside a TTL make zero HTTP calls; a network failure still renders the last good data with the amber badge.

---

## 5. Acceptance Criteria

All items are binary. The verifier checks each and reports failures to the owning agent.

### Phase 0 / schema

- [ ] `COMMON_COLUMNS` contains `is_stale` as its tenth entry and `validate()` rejects a frame without it.
- [ ] Both existing clients return `is_stale` as a bool column, `False` on a live fetch, and `validate()` passes on their output.
- [ ] `RIKSBANK_NOTES.md` records the `is_stale` addition and the date convention chosen for Q/M rows.

### Fixtures

- [ ] `fixtures.fixture_macro_df()` returns all 7 series, at least 40 quarterly / 120 monthly rows each, some `is_forecast=True` rows, passes `validate()` and the cleaning rules in 3.5.
- [ ] `fixtures.fixture_market_df()` returns all 19 tickers with at least 250 daily rows each plus at least one session of 5-minute rows, passes `validate()` and the cleaning rules in 3.5.
- [ ] Neither fixture function makes a network call (verified under a socket-blocking test).

### Riksbank client (riksbank-data-expert)

- [ ] `fetch_macro_series()` output passes `schema.validate()` with zero nulls in `date` / `metric_id` / `value`.
- [ ] Output has no duplicate `(date, metric_id)` rows.
- [ ] `metric_id` values are exactly the 7 codes in `RIKSBANK_SERIES`; `unit`, `category`, `frequency` match the constants for every row.
- [ ] Earliest non-forecast `date` per series is recorded in `RIKSBANK_NOTES.md`; any series with less than 10 years of actuals is listed by name.
- [ ] With HTTP mocked to fail 3 times, the client returns the on-disk snapshot with `is_stale=True` on every row and raises nothing.
- [ ] With HTTP mocked to fail and no snapshot present, the client returns an empty DataFrame with all `COMMON_COLUMNS` and raises nothing.
- [ ] A successful live fetch overwrites the snapshot file.

### Macro transforms (riksbank-data-expert)

- [ ] `latest_actuals(macro_df)` returns exactly one row per series with `is_forecast=False`.
- [ ] `cycle_state(macro_df)` returns a label from a fixed set of at most 5 values plus a one-line rationale string, and is deterministic on the fixture.
- [ ] Gap calculations (GDP gap, CPIF vs 2% target, CPIF excl. energy vs 2%) return one float per series and are unit-tested on the fixture.

### Yahoo client (yahoo-data-expert)

- [ ] `fetch_market_data()` output passes `schema.validate()` with zero nulls in `date` / `metric_id` / `value`.
- [ ] Output has no duplicate `(date, metric_id)` rows and drops all-NaN ticker-days rather than passing them through.
- [ ] `metric_id` values are a subset of `ALL_TICKERS`; `category` equals `SECTOR_BY_TICKER[metric_id]` and `unit == "SEK"` on every row.
- [ ] `get_ticker_history(t, "1d", "5m")` returns more than one distinct `date` value per ticker (normalize bug fixed) with `frequency == "5min"`.
- [ ] With one ticker mocked to fail 3 times, the other 18 are returned live and the failed ticker's rows come from the snapshot with `is_stale=True` (or are absent if no snapshot exists); no exception propagates.
- [ ] A successful live fetch overwrites the snapshot file.

### Market transforms (yahoo-data-expert)

- [ ] `equal_weighted_sector_index(market_df, sector)` returns a `pd.Series` indexed by date, rebased to 100 at its first date, for each of the 8 sectors; a sector with one ticker equals that ticker rebased.
- [ ] `returns_table(market_df)` returns one row per ticker with columns for 1d, 1w, 1m, YTD, 1y returns; missing windows are NaN, not 0.
- [ ] Returns are computed from `is_forecast=False` daily rows only (intraday rows excluded).

### Overlay transforms (macro-market-expert)

- [ ] `align_macro_market(macro_df, market_series, metric_id)` returns a frame with one row per market date and no nulls after the first macro observation.
- [ ] Forecast rows are excluded from alignment.
- [ ] `OVERLAY_NOTES.md` states the alignment method and the correlation window definition.
- [ ] Overlay functions run on the two fixtures without error for every macro series x every sector index.

### Macro Overview tab

- [ ] Each of the 7 tiles shows a latest actual value, its date, and a sparkline.
- [ ] Tiles are visually grouped under Growth / Labour / Prices / Policy headings.
- [ ] The State-of-the-cycle tile shows a label and a one-line rationale.
- [ ] Toggling "show forecasts" adds/removes the dashed forecast segment without changing the latest-value number.

### Stock Market tab

- [ ] Exactly 9 sub-tabs render: 8 sectors plus "All sectors".
- [ ] Switching sector sub-tabs updates both the performance table and its chart.
- [ ] Every sector chart includes the equal-weighted index line, labelled as such.
- [ ] Changing the lookback selector changes the chart x-range and the table window.

### Macro x Market tab

- [ ] Selecting any macro series and any market series renders a dual-axis chart with no exception.
- [ ] The correlation value and its window are displayed and change when the window selector changes.
- [ ] The caption states the alignment method.

### Sidebar / caching

- [ ] Clicking Refresh-now triggers new HTTP calls (observable via mocked request counter) and updates both badges' timestamps.
- [ ] Two reruns within the TTL without Refresh-now make zero HTTP calls.
- [ ] With Yahoo mocked to fail and a snapshot present, the Yahoo badge is amber and the Stock Market tab still renders; the Riksbank badge stays green.
- [ ] With a source mocked to fail and no snapshot, that badge is red and its tab shows an "unavailable" message instead of a traceback.
- [ ] Fixtures-mode toggle renders all three tabs with the network blocked.

### Tests, lint, run

- [ ] `uv run pytest` passes with all HTTP mocked (no live network).
- [ ] `uv run ruff check .` reports zero errors.
- [ ] `uv sync` followed by `uv run streamlit run app.py` boots on Python >= 3.14 without a traceback in the terminal.
- [ ] `README.md` contains the run command, the test command, and a one-paragraph description of each tab.

---

## 6. Non-Goals

- No cloud deployment; local `uv run streamlit run app.py` only.
- No background scheduler or daemon; data refreshes only on app rerun / Refresh-now / TTL expiry.
- No authentication, user accounts or per-user settings.
- No v2-deferred macro series (employed persons, KIX exchange rate, fiscal balance) and no tickers beyond the 19 in `constants.py`.
- No data editing, upload, or export to external systems (a CSV download button is out of scope for MVP).
- No real-time or sub-hourly *app refresh* cadence. Displaying already-fetched 5-minute intraday bars in a "last session" view is in scope; live streaming or auto-polling is not.
- No `yfinance` package usage (SSL failure on the dev machine).
- No multi-vintage Riksbank comparison (forecast revisions across policy rounds); one vintage per fetch.
- No company-name, market-cap, or fundamentals lookup for tickers; `metric_id` is the display name.
- No standalone cache module, database, or Redis; caching is `st.cache_data` plus per-client on-disk snapshots only.
- No portfolio tracking, alerts, or trade signals; correlation is descriptive, not predictive.
- No mobile-specific layout; desktop browser at 1280px+ is the target.
