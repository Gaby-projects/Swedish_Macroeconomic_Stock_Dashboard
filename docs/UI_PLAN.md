# UI_PLAN.md — Layout, Sidebar, Interaction, Empty/Error States

Ground truth: repo-root `CLAUDE.md` (structure, ownership) and `SPEC.md` (features F1–F5, acceptance criteria). This document specifies layout and behavior only — no implementation code. Chart-by-chart mapping lives in `CHART_SPECS.md`.

References `constants.py`: `RIKSBANK_SERIES` (7 codes, 4 categories: Growth, Labour, Prices, Policy), `TICKERS_BY_SECTOR` (19 tickers, 8 sectors), `SECTOR_BY_TICKER`. References `data/schema.py` `COMMON_COLUMNS` only.

---

## 0. Page shell

- `st.set_page_config(layout="wide")`. Page title: "Swedish Macro + Stock Market Dashboard".
- Top of page: title + one-line subtitle ("Sweden's macro cycle next to the Stockholm equity market").
- Below the title: three tabs in a fixed order — **Macro Overview**, **Stock Market**, **Macro × Market** — matching F1, F2, F3.
- Sidebar (left, persistent across all tabs) — see Section 1.

---

## 1. Sidebar (F4 Sidebar controls and freshness indicators, F5 Caching layer)

Top to bottom:

1. **App title / logo text** (static).
2. **Refresh now** button (`st.button`).
   - On click: clears all `st.cache_data` entries for both source-fetch wrappers and triggers a rerun.
   - Behavior: bypasses both the 6h macro TTL and the 15min market TTL (F5). Always makes fresh HTTP calls to both sources.
   - While a refresh is in flight, show a `st.spinner("Refreshing data…")` and disable the button.
3. **Freshness badges** — exactly two, one per source, always both visible regardless of active tab:
   - **Riksbank** badge
   - **Yahoo** badge
   - Each badge shows: source name, a colored status dot/pill, and `last_updated` (max over that source's rows, rendered as local time, e.g. "Updated 09:14 today").
   - Status colors (driven by `is_stale` and row count, not by tab state):
     - 🟢 **Green — Live**: frame non-empty and `is_stale == False` for all rows of that source.
     - 🟠 **Amber — Stale snapshot**: frame non-empty and `is_stale == True` for at least one row of that source. Caption: "Showing last saved snapshot from {last_updated}."
     - 🔴 **Red — Unavailable**: frame empty for that source. Caption: "No data available. Live fetch failed and no snapshot exists."
   - Badges update on every rerun (including after Refresh-now and after TTL-driven refetches).
4. **Fixtures mode** toggle (`st.toggle`, default **off**).
   - When on: both source DataFrames are swapped for `fixtures.fixture_macro_df()` / `fixtures.fixture_market_df()`; no network calls occur even if Refresh-now is pressed while it's on.
   - When on, both badges show a third state: ⚪ **Fixtures** (grey dot), caption "Fixtures mode — network disabled", `last_updated` omitted or replaced with "n/a (fixture data)".
5. **Show forecasts** toggle (`st.toggle`, default **off**). Scope: Macro Overview tab only, but lives in the sidebar since it's a global reading preference. Only affects sparkline rendering (adds/removes the dashed forecast segment); never changes any displayed "latest value" number (that always excludes `is_forecast=True` rows per `macro_transforms.latest_actuals`).
6. A short static caption at the bottom: data-source attribution ("Riksbank Monetary Policy Data API · Yahoo Finance chart API") and non-goals note ("Local/demo tool — not investment advice").

Controls that are **tab-local** (not in the sidebar, live inline in the tab body since they only affect that tab):
- **Lookback selector** (1m / 6m / 1y / 5y / max) — Stock Market tab, F2.
- **Window selector** (1y / 3y / 5y / max) — Macro × Market tab, F3.
- **Macro-series selector** (7 options) and **Market-series selector** (19 tickers + 8 sector indices) — Macro × Market tab, F3.
- **Sector sub-tab selector** — Stock Market tab (`st.tabs`, 9 sub-tabs), F2.

---

## 2. Tab 1 — Macro Overview (F1)

Top to bottom, single column of grouped sections:

1. **State-of-the-cycle summary tile** — full width, rendered first, visually distinct (bordered container, larger than the metric tiles below).
   - Composite label (one of a fixed set of ≤5 values from `macro_transforms.cycle_state()`) shown as a colored badge/pill.
   - One-line rationale string beneath the label.
   - Three small supporting metric chips inline beneath the rationale, from `macro_transforms.gap_metrics()`: GDP gap, CPIF gap vs 2% target, CPIF excl. energy gap vs 2% target, plus the latest policy rate value (from `latest_actuals`) shown as a fourth chip.
   - If the Riksbank source is stale/unavailable, this tile shows the corresponding degraded state (Section 5) instead of a stale composite label — a cycle read from stale/missing data must say so, not present a confident-looking badge.
2. **Four category sections**, each a `st.subheader` followed by a row of tiles for the series in that category (`st.columns`):
   - **Growth** — GDP (`SEQGDPNAYSA`), GDP gap (`SEQGDPGAPYSA`) — 2 tiles.
   - **Labour** — Unemployment rate (`SEQLABUEASA`) — 1 tile.
   - **Prices** — CPIF (`SEMCPIFNAYNA`), CPIF excl. energy (`SEMCPIFFEXYNA`) — 2 tiles.
   - **Policy** — Policy rate (`SEQRATENAYNA`), Policy rate expectations (`SEDMENAYNA`) — 2 tiles.
   - Each tile (see `CHART_SPECS.md` for the sparkline spec): series description (from `RIKSBANK_SERIES`), latest actual value + unit, observation date, a data-span caption ("Actuals since {earliest date}" — states the real span rather than assuming 10y), and a 10y sparkline.
3. Order within a category row follows the order series appear in `RIKSBANK_SERIES`.

---

## 3. Tab 2 — Stock Market (F2)

1. **Lookback selector** at the top of the tab, above the sub-tabs (`st.radio` or `st.segmented_control`, options: 1m / 6m / 1y / 5y / max, default 1m). Applies to every sector sub-tab's chart x-range and table return-window emphasis simultaneously — it is one control shared by all 9 sub-tabs, not re-shown per sub-tab.
2. **9 sub-tabs** (`st.tabs`), in this fixed order: Industrials, Telecom/Tech, Banking, Investment/Holding, Consumer, Healthcare, Materials/Mining, Gaming, **All sectors**.
3. **Each of the 8 sector sub-tabs** contains, top to bottom:
   a. **Performance table** — one row per ticker in that sector (`market_transforms.returns_table()` filtered to the sector). Columns: Ticker, Latest close (SEK), 1d %, 1w %, 1m %, YTD %, 1y %. Tickers with no data in a window show "n/a", never 0 or a blank cell.
   b. **Rebased-to-100 line chart** — one line per ticker in the sector plus the equal-weighted sector index line (bold, labelled "Sector index (equal-weighted)"), over the selected lookback window.
   c. **Last session (intraday) strip** — a row of small per-ticker sparklines using 5-minute bars for the current trading session (`get_ticker_history(ticker, "1d", "5m")`). See Section 5 / `CHART_SPECS.md` for the market-closed fallback.
4. **"All sectors" sub-tab** contains:
   a. **Sector performance table** — one row per sector (`market_transforms.sector_returns_table()`): Sector, Latest index level, 1d/1w/1m/YTD/1y % returns of the equal-weighted index.
   b. **All-sector-indices chart** — 8 lines, one equal-weighted index per sector, all rebased to 100, over the selected lookback window.
5. Switching sub-tabs updates that sub-tab's table and chart only (each sub-tab computes independently; no shared chart state across sub-tabs other than the lookback selector). Changing the lookback selector re-renders the table window and every chart's x-range on whichever sub-tab is currently active (and on revisit for the others, since Streamlit re-executes the active tab body on rerun).

---

## 4. Tab 3 — Macro × Market (F3)

Single-column layout, top to bottom:

1. **Three inline selectors** in a row (`st.columns(3)`):
   - **Macro series** (`st.selectbox`, 7 options from `RIKSBANK_SERIES`, labelled by description) — default `SEQRATENAYNA` (Policy rate).
   - **Market series** (`st.selectbox`, 19 tickers + 8 sector indices, tickers grouped under an "Individual tickers" optgroup-style label and indices under "Sector indices" if `st.selectbox` grouping isn't available, simulate via a prefixed label e.g. "Index: Banking") — default "Index: Banking".
   - **Window** (`st.radio`, 1y / 3y / 5y / max, default 1y).
   - Two "suggested pairs" quick-select buttons above the row for the two required examples from the task brief: **"Policy rate vs. Banking index"** and **"CPIF vs. H&M"** — clicking one sets both selectors accordingly.
2. **Dual-axis overlay chart** (see `CHART_SPECS.md`) — full width, directly below the selectors.
3. **Correlation readout** — a single `st.metric`-style value (e.g. "Rolling correlation (1y): 0.42") directly under the chart, updating whenever the window selector changes.
4. **Alignment caption** — one line of italic text under the correlation readout stating the alignment method exactly as documented in `OVERLAY_NOTES.md` (e.g. "Macro values are forward-filled onto market trading days from `align_macro_market()`."). This caption is copied from `OVERLAY_NOTES.md` at build time, not invented independently.
5. Forecast rows (`is_forecast=True`) are excluded from the overlay entirely (no dashed segment here, unlike Macro Overview sparklines) — the overlay is actuals-only per F3.

---

## 5. Empty / error / stale states

Applies per source, independent of tab (a Riksbank failure never blocks the Stock Market tab and vice versa, per SPEC 2.3):

| Source state | Sidebar badge | Macro Overview tab | Stock Market tab | Macro × Market tab |
|---|---|---|---|---|
| Riksbank live, Yahoo live | Both green | Full tiles + summary tile | Full tables + charts | Full overlay chart |
| Riksbank stale snapshot, Yahoo live | Riksbank amber, Yahoo green | Tiles render normally but each tile caption adds "· stale" in amber; summary tile keeps its label but appends "(based on last saved data from {date})" | Unaffected | Chart renders; caption adds a one-line amber note "Macro series is a stale snapshot from {date}." |
| Riksbank unavailable (empty), Yahoo live | Riksbank red, Yahoo green | Replace the whole tab body with a centered `st.warning`: "Macro data unavailable — live fetch failed and no snapshot exists. Try Refresh now." No tiles, no traceback. | Unaffected | Macro selector disabled or shows "No macro data available"; chart area shows the same warning instead of a chart. |
| Yahoo stale snapshot, Riksbank live | Yahoo amber, Riksbank green | Unaffected | Tables/charts render; every value sourced from stale rows gets a small amber "stale" tag next to the ticker/sector name; a top-of-tab caption states "Some or all market data is from a saved snapshot." | Market selector still populated; overlay caption adds the same amber note as above for the market leg. |
| Yahoo unavailable (empty), Riksbank live | Yahoo red, Riksbank green | Unaffected | Replace tab body with `st.warning`: "Market data unavailable — live fetch failed and no snapshot exists. Try Refresh now." | Market selector disabled/shows "No market data available"; chart area shows the warning. |
| One ticker fails, others live (per-ticker stale/absent) | Yahoo stays green if no row anywhere is stale and the ticker is simply absent; amber if the ticker's rows came back stale | Unaffected | The affected ticker's table row shows "n/a" across all returns and a "stale"/"missing" tag; it is never silently dropped from the table (row stays, values are "n/a"); its line is omitted from the rebased chart if no data exists at all, with a one-line caption below the chart listing any tickers omitted for this reason | Unaffected unless that ticker is the selected market series, in which case the same per-tab warning pattern applies |
| Both sources unavailable | Both red | Warning as above | Warning as above | Both selectors disabled; warning shown in place of the chart |
| Fixtures mode on | Both grey/"Fixtures" | Renders exactly as the live-good case, using fixture data, with a small persistent caption at the top of every tab: "Fixtures mode — showing sample data, network disabled." | Same | Same |

Additional state — **market closed / no intraday bars** (Stock Market tab, "Last session" strip only, independent of the table above): when `get_ticker_history(ticker, "1d", "5m")` returns zero rows for the current session (outside 09:00–17:30 CET/CEST, weekend, or holiday), that ticker's intraday sparkline slot is grayed out with the label "Market closed" instead of a chart. This never removes the ticker from the sector — the rebased 3-window charts and performance table are unaffected, only the intraday strip degrades.

General rule: no state ever shows a Python traceback. Empty-frame handling always resolves to one of the `st.warning`/"n/a"/"Market closed" states above.

---

## 6. Interaction spec (summary)

| Control | Location | Updates on change |
|---|---|---|
| Refresh now | Sidebar | Clears cache, refetches both sources, both badges, all three tabs' content on next render |
| Fixtures mode toggle | Sidebar | Swaps data source for all three tabs and both badges; no HTTP calls while on |
| Show forecasts toggle | Sidebar | Macro Overview sparklines only (dashed forecast segment appears/disappears); tile latest-value numbers unchanged |
| Sector sub-tab | Stock Market tab | That sub-tab's performance table and chart(s) only |
| Lookback selector | Stock Market tab | Table window emphasis + chart x-range for whichever sub-tab is active (and any sub-tab visited afterward) |
| Macro-series selector | Macro × Market tab | Left y-axis line + macro leg of the correlation + alignment caption's macro name |
| Market-series selector | Macro × Market tab | Right y-axis line + market leg of the correlation |
| Window selector | Macro × Market tab | Chart x-range and the correlation value |
| "Suggested pairs" buttons | Macro × Market tab | Sets both the macro-series and market-series selectors, then re-renders the chart as if selected manually |
