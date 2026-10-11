# Swedish Macro + Stock Market Dashboard

Interactive Streamlit dashboard combining Swedish macroeconomic indicators (Riksbank) with Swedish equity data (Yahoo Finance). Dual purpose: a portfolio piece demonstrating a multi-source data dashboard, and a genuinely usable tool for tracking Sweden's macro cycle alongside its equity market.

This file is the shared ground truth for every agent in `.claude/agents/`. Agent files reference it; they should not restate or contradict it. If something here turns out to be wrong, fix it here first.

## Layout and run

- Package: `swedish_macro_dashboard`, src layout at `src/swedish_macro_dashboard/`. All imports are `from swedish_macro_dashboard...`; never `from src...`.
- Entry point: `app.py` at the repo root.
- Run: `uv run streamlit run app.py`. Python >= 3.14, managed with `uv`.
- Tests: `uv run pytest` (mocked HTTP, no live network).
- Lint: `uv run ruff check .` (must pass with no errors).

## What exists today

| Path (under `src/swedish_macro_dashboard/`) | Status |
|---|---|
| `data/schema.py` | `COMMON_COLUMNS` (incl. `is_stale`), `validate()` |
| `constants.py` | `RIKSBANK_SERIES` (7), `TICKERS_BY_SECTOR` (19 tickers / 8 sectors) |
| `data/riksbank_client.py` | `fetch_macro_series(series_ids=None)`, with retry, snapshot fallback and cleaning |
| `data/yahoo_client.py` | `fetch_market_data()`, `get_ticker_history(ticker, range_, interval)`, with retry, snapshot fallback and cleaning |
| `data/fixtures.py` | `fixture_macro_df()`, `fixture_market_df()`: seeded sample data for Fixtures mode and tests |
| `transforms/` | `macro_transforms.py`, `market_transforms.py`, `overlay_transforms.py` |
| `tabs/` | `macro_overview.py`, `stock_market.py`, `macro_market.py`, shared `components.py` |

Planning docs live in `docs/`: `SPEC.md`, `tasks.json`, `UI_PLAN.md`, `CHART_SPECS.md`, `RIKSBANK_NOTES.md`, `OVERLAY_NOTES.md`, `api-research.md`.

## Data-source facts (verified live)

- **Riksbank Monetary Policy Data API** (`https://api.riksbank.se/monetary_policy_data/v1`): no subscription key required. Series are fetched per policy-round vintage; each row is flagged `is_forecast`. One vintage holds more than 10 years of actuals for every series in scope (see `docs/RIKSBANK_NOTES.md`).
- **Yahoo Finance**: the client calls the chart endpoint (`https://query1.finance.yahoo.com/v8/finance/chart/{ticker}`) directly via `requests`. It deliberately does **not** use the `yfinance` package — its `curl_cffi` backend can fail SSL verification behind a corporate proxy or a locally-trusted root CA. No auth. `range=1d&interval=5m` returns 5-minute intraday bars for OMX Stockholm tickers, current session only (no pre/post-market).
- Only the series codes and tickers in `constants.py` are in scope. No agent adds new ones; changes go back to pm-plan.

## Shared contract

`COMMON_COLUMNS` in `data/schema.py` is the long-format contract every source conforms to. pm-plan decides the contract; **riksbank-data-expert is the only agent that edits `schema.py`**. Every source client validates and cleans its own output (`schema.validate()`, dedup on `(date, metric_id)`, dtype coercion, null handling) before returning; downstream code does not re-clean.

`is_stale` (bool, default `False`) in `COMMON_COLUMNS` is `True` only when a client returns an on-disk snapshot after a failed live fetch. There is no standalone cache module: snapshot fallback lives inside each client; TTL caching is `st.cache_data(ttl=...)` in the app.

## Agents and ownership

| Agent | Owns |
|---|---|
| `pm-plan` | `docs/SPEC.md`, `docs/tasks.json` (written to disk). No code. |
| `riksbank-data-expert` | `schema.py` changes, `fixture_macro_df()`, `riksbank_client.py`, `transforms/macro_transforms.py`, `docs/RIKSBANK_NOTES.md`, Riksbank section of `docs/api-research.md` |
| `yahoo-data-expert` | `fixture_market_df()`, `yahoo_client.py`, `transforms/market_transforms.py` (incl. `equal_weighted_sector_index(market_df, sector) -> pd.Series`), Yahoo section of `docs/api-research.md` |
| `viz-ux-designer` | `docs/UI_PLAN.md`, `docs/CHART_SPECS.md`. No code. |
| `macro-market-expert` | `transforms/overlay_transforms.py`, `docs/OVERLAY_NOTES.md` |
| `streamlit-builder` | `app.py`, `tabs/*`, `st.cache_data` wiring, `README.md`, `pyproject.toml` deps it needs |
| `verifier` | Nothing — read-only. Runs checks and delegates failures to the owning agent. |

`data/fixtures.py` and `docs/api-research.md` are shared between the two data agents: each adds its own function/section and never overwrites the other's.

## Execution order

1. `pm-plan` → writes `docs/SPEC.md` + `docs/tasks.json`.
2. **Phase 0** (`riksbank-data-expert`): add `is_stale` to `schema.py` and set it in both clients. Nothing else starts until this lands.
3. **Parallel**: `riksbank-data-expert` · `yahoo-data-expert` · `viz-ux-designer`. Each data agent builds its fixture half first, then its client/transforms. viz-ux-designer can start before `docs/SPEC.md` exists using the structure in its own file.
4. `macro-market-expert` → needs `macro_transforms.py` and `market_transforms.py` (or works against stubs until they land).
5. `streamlit-builder` → needs `docs/SPEC.md`, `docs/UI_PLAN.md`, `docs/CHART_SPECS.md`, and all three transform modules.
6. `verifier` → after any agent finishes code, and always after `streamlit-builder`.

## Conventions

- Every agent flags a blocker back to the owner rather than working around it (no redefining the schema, inventing tickers, or building a module someone else owns).
- Agent-facing notes files (`*_NOTES.md`, `docs/api-research.md`) are for verified findings only.
- `verifier` never edits anything, not even to fix a one-liner.
