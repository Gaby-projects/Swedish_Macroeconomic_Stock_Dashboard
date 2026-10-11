# CHART_SPECS.md — Per-Chart Specification

Ground truth: repo-root `CLAUDE.md`, `SPEC.md` (F1–F5, acceptance criteria), `UI_PLAN.md` (layout this feeds into). No implementation code. All field names below are `data/schema.py` `COMMON_COLUMNS` (`date`, `metric_id`, `value`, `unit`, `category`, `source`, `frequency`, `last_updated`, `is_forecast`, `is_stale`) or named outputs of `transforms/*` functions from `SPEC.md` / `tasks.json`.

## Charting library decision

**Plotly** (`plotly.graph_objects` / `plotly.express`, rendered via `st.plotly_chart`) for **every** chart in this document, including sparklines. Rationale: it is the only library in this stack that supports dual y-axes (required for F3) and per-segment line styling (solid actual / dashed forecast, required for F1); using one library everywhere avoids streamlit-builder having to maintain two charting APIs for visually similar line charts. **Plotly is not yet a dependency** — `tasks.json` T002 already names it as the default and assigns adding it to `streamlit-builder`. Tables (performance tables, sector table) use native `st.dataframe`, not a charting library. The State-of-the-cycle label and gap-metric chips are rendered as styled text/`st.metric`, not charts.

---

## Macro Overview (F1)

### C1. Macro tile sparkline (×7, one per `RIKSBANK_SERIES` code)

- **Type:** Plotly line chart, compact (no axis labels/ticks shown, height ~80–100px), embedded under the tile's latest-value text.
- **Input:** `macro_transforms.sparkline_series(macro_df, metric_id, include_forecast)`, called once per series. Underlying columns: `date` (x), `value` (y), `is_forecast` (segment split).
- **X:** `date`, full available history up to 10y (per-series span may be shorter — label states the real span per `UI_PLAN.md` §2).
- **Y:** `value`, in the series' `unit`; no numeric axis shown, only the line shape (a sparkline).
- **Series split:** two traces from one `metric_id`:
  - Trace A — rows where `is_forecast == False`: solid line, single accent color (one consistent color for all macro sparklines, e.g. Plotly's default blue, to keep tiles visually uniform).
  - Trace B — rows where `is_forecast == True`: only drawn when the sidebar "show forecasts" toggle is on; dashed line, same hue as Trace A but lower opacity (e.g. 50%); starts at the last actual point so the two segments connect visually.
  - Toggling "show forecasts" off removes Trace B entirely; Trace A and the tile's latest-value number never change.
- **Color rule — `is_stale`:** if the Riksbank source's `is_stale == True` for this series, Trace A is rendered in a muted grey instead of the accent color, and the tile caption (outside the chart) states "stale" per `UI_PLAN.md` §5. The sparkline itself always renders if any snapshot data exists; only an empty frame produces the "unavailable" state instead of a grey sparkline.
- **Legend:** none (tile context makes the series identity obvious from the surrounding label).
- **Empty state:** if `sparkline_series()` returns zero rows for a `metric_id` (only possible if the whole Riksbank source is unavailable), the tile shows no chart, just the unavailable caption — do not render an empty Plotly canvas.

### C2. State-of-the-cycle summary tile

- **Type:** not a chart — a styled badge/label component (`st.markdown` with a colored background, or `st.container(border=True)` + `st.metric`).
- **Input:** `macro_transforms.cycle_state(macro_df)` → `(label: str, rationale: str)`; `macro_transforms.gap_metrics(macro_df)` → GDP gap, CPIF gap vs 2%, CPIF excl. energy gap vs 2% (floats); `macro_transforms.latest_actuals(macro_df)` filtered to `SEQRATENAYNA` for the policy-rate chip.
- **Visual mapping:** `label` → pill/badge text, color from a fixed lookup keyed to the label value (e.g. expansion-like labels green, contraction-like labels red, neutral/mixed amber — exact label set and color assignment finalized once `cycle_state()`'s fixed ≤5-value set is known; streamlit-builder maps each returned label string to one of {green, amber, red, grey} and documents the mapping in code comments, no new labels invented). `rationale` → plain text line under the badge. The 4 chips (GDP gap, CPIF gap, CPIF excl. energy gap, policy rate) render as four `st.metric`-style values in a row, each showing the number + unit, no chart.
- **Stale/unavailable:** if any input series feeding `cycle_state()`/`gap_metrics()` is stale, the badge keeps its color but appends "(stale)" in the rationale line; if the whole Riksbank source is unavailable, the tile shows the unavailable message from `UI_PLAN.md` §5 instead of a badge.

---

## Stock Market (F2)

### C3. Performance table (×9 — one per sector sub-tab, one for "All sectors")

- **Type:** `st.dataframe` (native Streamlit table), not a chart.
- **Per-sector table input:** `market_transforms.returns_table(market_df)` filtered to tickers in the active sector via `SECTOR_BY_TICKER`.
  - **Columns:** Ticker (`metric_id`), Latest close (SEK) (`value` at max `date` where `frequency == "D"` and `is_forecast == False`), 1d %, 1w %, 1m %, YTD %, 1y % (from `returns_table()`).
  - Missing-window cells render "n/a" (never 0/blank), per acceptance criteria.
  - Row order: fixed ticker order as listed in `TICKERS_BY_SECTOR[sector]`.
- **"All sectors" table input:** `market_transforms.sector_returns_table(market_df)`. **Columns:** Sector, Latest index level, 1d/1w/1m/YTD/1y % (of the equal-weighted index). Row order: fixed sector order as listed in `TICKERS_BY_SECTOR` keys.
- **Stale/error tagging:** any row where the underlying ticker's most recent contributing rows have `is_stale == True` gets a small amber "stale" text tag appended to the Ticker/Sector cell (rendered as `"VOLV-B.ST ⚠"` or similar, not a separate column, to avoid inventing a schema field).

### C4. Sector rebased-to-100 chart (×8, one per sector sub-tab)

- **Type:** Plotly multi-line chart (`go.Figure` with one `go.Scatter` trace per ticker plus one for the index).
- **Input:** `market_transforms.rebase_to_100(market_df, tickers=<sector's tickers>, start=<lookback start date>)` for the per-ticker lines; `market_transforms.equal_weighted_sector_index(market_df, sector)` (already rebased to 100 at its first date, per its own contract) for the index line, re-sliced to the same lookback window.
- **X:** `date`, restricted to the active lookback window (1m/6m/1y/5y/max, from the shared lookback selector). Daily rows only (`frequency == "D"`, `is_forecast == False` — market rows are never forecasts).
- **Y:** rebased value (index = 100 at window start), one axis, linear scale, label "Rebased to 100".
- **Color/legend rule:** each ticker gets a distinct color from a fixed qualitative palette (consistent ticker→color mapping across the session so the same ticker is always the same color if it appears elsewhere); the equal-weighted index line is bold/thicker (e.g. width 3 vs 1.5) and always black or a dark neutral, labelled exactly "Sector index (equal-weighted)" in the legend so it's unambiguous per the acceptance criteria ("labelled as such"). Legend shown, positioned outside the plot area (right or below) since a 6-ticker sector (Industrials) plus the index is 7 lines.
- **Stale rendering:** a ticker whose window data is entirely stale-snapshot rows is drawn dashed instead of solid, with "(stale)" appended to its legend entry; if a ticker has zero rows in the window it is omitted from the chart entirely (per `UI_PLAN.md` §5) and named in a caption below the chart ("No data in this window for: X").
- **Empty state:** if the whole Yahoo source is unavailable for this sector, no chart renders — `UI_PLAN.md` §5 unavailable message shows instead.

### C5. All-sector-indices chart ("All sectors" sub-tab)

- **Type:** Plotly multi-line chart, same construction as C4 but with exactly 8 traces (one `equal_weighted_sector_index()` per sector) and no individual tickers.
- **X:** `date`, restricted to the active lookback window.
- **Y:** rebased value (index = 100 at window start), one axis, label "Rebased to 100".
- **Color/legend:** one fixed color per sector (8-color qualitative palette), legend labelled by sector name exactly as in `TICKERS_BY_SECTOR` keys. No line is bolded (all 8 are peers here, unlike C4 where the index is singled out against its own tickers).
- **Stale rendering:** same dashed convention as C4, applied per sector if any of its constituent tickers contributed stale rows to that sector's index for the window.

### C6. Last-session intraday sparkline strip (per ticker, within each sector sub-tab)

- **Type:** Plotly line chart, compact sparkline (same visual treatment as C1 — no axes/ticks), one per ticker, laid out in a row of small tiles below the C4 chart.
- **Input:** `get_ticker_history(ticker, range_="1d", interval="5m")` output, filtered to `metric_id == ticker`.
- **X:** `date` (full intraday timestamp, 5-minute bars — not normalized, per the fixed `.normalize()` bug), current session only.
- **Y:** `value` (close price, SEK), no numeric axis, sparkline shape only.
- **Color rule:** green if the session's last value ≥ first value, red if lower (simple up/down day coloring, common sparkline convention) — this is a display-only derived comparison, not a new schema field.
- **Window:** always "current trading session" — this chart has no lookback-selector dependency (it is intentionally independent of the 1m/6m/1y/5y/max control, since intraday data does not exist outside the current session).
- **Market-closed fallback:** if `get_ticker_history` returns zero rows for the session (outside 09:00–17:30 CET/CEST, weekend, or Swedish market holiday — no pre/post-market data per `hasPrePostMarketData`), this tile's chart area is replaced with a grayed-out placeholder box containing the label "Market closed" in muted text. The ticker's slot still appears (fixed grid position) so the strip's layout doesn't jump between sessions; it just shows the placeholder instead of a line.
- **Stale rendering:** if the ticker's intraday rows came from a snapshot (`is_stale == True`), render the sparkline dashed with a small "stale" caption under the tile instead of the up/down color rule (staleness takes visual precedence over the up/down color, since a stale intraday session is not meaningfully "today's" move).

---

## Macro × Market (F3)

### C7. Dual-axis overlay chart

- **Type:** Plotly dual-y-axis line chart (`go.Figure` with two `go.Scatter` traces, `yaxis` and `yaxis2` via `fig.update_layout(yaxis2=dict(overlaying="y", side="right"))`).
- **Input:** `overlay_transforms.overlay_frame(macro_df, market_df, macro_metric_id, market_series_id, window)` (name per `OVERLAY_NOTES.md` once `macro-market-expert` lands it — must expose one row per market date over the selected window with a macro value column and a market value column, aligned via `align_macro_market()`).
- **X:** `date` (market calendar — the alignment target per `OVERLAY_NOTES.md`), restricted to the selected window (1y/3y/5y/max).
- **Left y-axis (`yaxis`):** macro series `value`, in its native `unit` (e.g. "Per cent" for policy rate, "Annual % change" for CPIF). Axis title = the macro series description from `RIKSBANK_SERIES`. Line color: fixed accent (e.g. blue), solid — forecast rows are excluded entirely from this trace per F3 (no dashed segment here, unlike C1).
- **Right y-axis (`yaxis2`):** market series `value` — either a raw ticker close price (SEK) or a sector index level (already rebased to 100 by `equal_weighted_sector_index`) depending on what's selected. Axis title = the ticker symbol or "{Sector} index (equal-weighted)". Line color: fixed contrasting accent (e.g. orange), solid.
- **Legend:** shown, two entries, one per axis/trace, labelled with the macro description and the market series name respectively.
- **Normalization:** **not applied** — this is a true dual-axis overlay (each series keeps its native scale/unit on its own axis), not a normalized-to-100 overlay, because the macro axis (percent) and market axis (price or index level) have fundamentally different units and a policy-rate-vs-price-level comparison is more legible on independent scales. If a rebased/normalized alternate view is wanted later, that is a new chart, not a modification of C7 (flag to pm-plan rather than inventing it here).
- **Stale rendering:** if either leg's source is `is_stale == True` for its contributing rows, that leg's line is drawn dashed and its axis title gets a trailing " (stale)".
- **Empty state:** if either `macro_df` or `market_df` is empty for the selected series, no chart renders — the tab shows the unavailable message from `UI_PLAN.md` §5 in the chart's place, and the selector for the missing side is disabled.

**Default / suggested pairs (must both be selectable, not hardcoded-only):**
- Macro = Policy rate (`SEQRATENAYNA`), Market = Banking sector index (`equal_weighted_sector_index(market_df, "Banking")`).
- Macro = CPIF (`SEMCPIFNAYNA`), Market = `HM-B.ST` (Consumer sector's single ticker).

### C8. Correlation readout

- **Type:** not a chart — `st.metric` showing a single scalar, e.g. "Rolling correlation (1y): 0.42".
- **Input:** `overlay_transforms.rolling_correlation(aligned_df, window)` — scalar for the currently selected window (matches C7's window selector so the two never disagree).
- **Update rule:** recomputes whenever the macro-series selector, market-series selector, or window selector changes; always paired with the alignment caption stating the method, copied verbatim from `OVERLAY_NOTES.md`.
- **Missing-data behavior:** if the aligned frame has fewer than 2 overlapping points in the window, show "n/a — insufficient overlapping data" instead of a number; never show `NaN` or a Python error string.

---

## Cross-cutting rules (apply to every chart above)

- **`is_forecast` styling:** dashed line, reduced opacity, only ever in Macro Overview sparklines (C1) when "show forecasts" is on. Never appears in C4–C7 (market data has no forecasts; the F3 overlay explicitly excludes forecast rows).
- **`is_stale` styling:** dashed line (macro/market line charts) or grey/muted tone + "(stale)"/"⚠" text tag (tiles, tables, badges) — never silently shown as if live. Exact convention per chart is specified above; streamlit-builder must not invent a different treatment per chart.
- **No chart ever renders on an empty input frame.** Every chart above has a named fallback (unavailable message, "Market closed" placeholder, omission + caption, or "n/a") — a blank Plotly canvas or a stack trace is never an acceptable empty state.
- **Axis/label units always come from `unit`/`category`/`metric_id`** in `COMMON_COLUMNS` or from `RIKSBANK_SERIES`/`TICKERS_BY_SECTOR` metadata — no chart introduces a display label that isn't traceable to those sources.
