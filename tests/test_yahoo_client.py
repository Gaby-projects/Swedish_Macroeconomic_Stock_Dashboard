"""Mocked-HTTP tests for data.yahoo_client. No live network is used."""

from __future__ import annotations

import pandas as pd
import pytest
import requests

from swedish_macro_dashboard.constants import ALL_TICKERS, SECTOR_BY_TICKER
from swedish_macro_dashboard.data import yahoo_client as yc
from swedish_macro_dashboard.data.schema import COMMON_COLUMNS, validate


class FakeResponse:
    def __init__(self, json_data, status_code: int = 200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json_data


def _chart_payload(timestamps: list[int], closes: list[float | None], currency: str = "SEK"):
    n = len(timestamps)
    quote = {
        "open": list(closes),
        "high": list(closes),
        "low": list(closes),
        "close": list(closes),
        "volume": [1000] * n,
    }
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": currency,
                        "exchangeTimezoneName": "Europe/Stockholm",
                        "hasPrePostMarketData": False,
                        "regularMarketPrice": closes[-1] if closes else None,
                    },
                    "timestamp": timestamps,
                    "indicators": {"quote": [quote]},
                }
            ],
            "error": None,
        }
    }


def _empty_chart_payload():
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "SEK",
                        "exchangeTimezoneName": "Europe/Stockholm",
                        "hasPrePostMarketData": False,
                    },
                    "timestamp": None,
                    "indicators": {"quote": [{}]},
                }
            ],
            "error": None,
        }
    }


# Two daily bars, one week apart, at noon UTC.
_DAILY_TIMESTAMPS = [1704110400, 1704715200]  # 2024-01-01, 2024-01-08 12:00 UTC
_DAILY_CLOSES = [100.0, 102.0]

# Five 5-minute bars within the same UTC day.
_INTRADAY_BASE = 1717398000  # 2024-06-03 07:00 UTC
_INTRADAY_TIMESTAMPS = [_INTRADAY_BASE + i * 300 for i in range(5)]
_INTRADAY_CLOSES = [50.0, 50.1, 50.2, 50.15, 50.3]


@pytest.fixture(autouse=True)
def _no_real_sleep():
    # Skip tenacity's actual backoff sleep so retry tests run instantly.
    yc._get_ticker_history_with_retry.retry.sleep = lambda seconds: None


@pytest.fixture(autouse=True)
def _isolated_snapshot(tmp_path, monkeypatch):
    snap_dir = tmp_path / "data_snapshots"
    snap_file = snap_dir / "yahoo_market.csv"
    monkeypatch.setattr(yc, "SNAPSHOT_DIR", snap_dir)
    monkeypatch.setattr(yc, "SNAPSHOT_FILE", snap_file)
    return snap_file


def _mock_all_succeed(monkeypatch, tickers, range_="1mo", interval="1d"):
    def fake_get(url, headers=None, params=None, timeout=15):
        assert any(t in url for t in tickers)
        return FakeResponse(_chart_payload(_DAILY_TIMESTAMPS, _DAILY_CLOSES))

    monkeypatch.setattr(requests, "get", fake_get)


# --- get_ticker_history --------------------------------------------------


def test_get_ticker_history_daily_normalizes_to_midnight(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResponse(_chart_payload(_DAILY_TIMESTAMPS, _DAILY_CLOSES))
    )
    df = yc.get_ticker_history("VOLV-B.ST", range_="1mo", interval="1d")

    assert (df["frequency"] == "D").all()
    assert (df["date"].dt.hour == 0).all()
    assert (df["date"].dt.minute == 0).all()
    assert df["date"].nunique() == 2


def test_get_ticker_history_intraday_preserves_timestamps(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: FakeResponse(_chart_payload(_INTRADAY_TIMESTAMPS, _INTRADAY_CLOSES)),
    )
    df = yc.get_ticker_history("VOLV-B.ST", range_="1d", interval="5m")

    assert (df["frequency"] == "5min").all()
    # The normalize() bug collapsed all 5 bars into a single date - assert
    # it's fixed: more than one distinct timestamp survives.
    assert df["date"].nunique() > 1
    assert df["date"].nunique() == len(_INTRADAY_TIMESTAMPS)


def test_get_ticker_history_market_closed_returns_empty_frame(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(_empty_chart_payload()))
    df = yc.get_ticker_history("VOLV-B.ST", range_="1d", interval="5m")

    assert df.empty
    assert list(df.columns) == yc._OHLCV_COLUMNS


# --- to_common_schema (cleaning) ------------------------------------------


def test_to_common_schema_dedups_coerces_and_drops_nulls():
    dirty = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-03"]
            ),
            "ticker": "VOLV-B.ST",
            "sector": "Industrials",
            "open": [1, 1, 1, 1],
            "high": [1, 1, 1, 1],
            "low": [1, 1, 1, 1],
            "close": [100.0, 101.0, None, 103.0],  # duplicate date + null close
            "volume": [10, 10, 10, 10],
            "currency": "SEK",
            "frequency": "D",
        }
    )

    result = yc.to_common_schema(dirty)

    validate(result, name="test")
    assert result["value"].notna().all()
    assert not result.duplicated(subset=["date", "metric_id"]).any()
    # duplicate 2024-01-01 rows collapse to the last (close=101.0);
    # the null-close 2024-01-02 row is dropped entirely.
    assert len(result) == 2
    kept = result.sort_values("date").reset_index(drop=True)
    assert kept.loc[0, "value"] == pytest.approx(101.0)


def test_to_common_schema_empty_input_returns_empty_common_columns():
    result = yc.to_common_schema(pd.DataFrame(columns=yc._OHLCV_COLUMNS))
    assert result.empty
    assert list(result.columns) == COMMON_COLUMNS


# --- fetch_market_data -----------------------------------------------------


def test_fetch_market_data_success_is_clean_and_validated(monkeypatch):
    tickers = ["VOLV-B.ST", "ERIC-B.ST"]
    _mock_all_succeed(monkeypatch, tickers)

    df = yc.fetch_market_data(tickers)

    validate(df, name="test")
    assert set(df["metric_id"]) == set(tickers)
    assert (df["is_stale"] == False).all()
    assert (df["unit"] == "SEK").all()
    for ticker in tickers:
        assert (df[df["metric_id"] == ticker]["category"] == SECTOR_BY_TICKER[ticker]).all()
    assert not df.duplicated(subset=["date", "metric_id"]).any()
    assert pd.api.types.is_float_dtype(df["value"])
    assert pd.api.types.is_datetime64_any_dtype(df["date"])


def test_successful_fetch_writes_snapshot(monkeypatch, _isolated_snapshot):
    tickers = ["VOLV-B.ST"]
    _mock_all_succeed(monkeypatch, tickers)

    assert not _isolated_snapshot.exists()
    yc.fetch_market_data(tickers)
    assert _isolated_snapshot.exists()

    on_disk = pd.read_csv(_isolated_snapshot)
    assert set(on_disk["metric_id"]) == set(tickers)


def test_one_ticker_failing_does_not_affect_others(monkeypatch, _isolated_snapshot):
    tickers = ["VOLV-B.ST", "ERIC-B.ST"]

    def fake_get(url, headers=None, params=None, timeout=15):
        if "ERIC-B.ST" in url:
            raise requests.ConnectionError("boom")
        return FakeResponse(_chart_payload(_DAILY_TIMESTAMPS, _DAILY_CLOSES))

    monkeypatch.setattr(requests, "get", fake_get)

    df = yc.fetch_market_data(tickers)

    # No snapshot exists yet for the failing ticker, so it's simply omitted;
    # the succeeding ticker is still returned live.
    assert set(df["metric_id"]) == {"VOLV-B.ST"}
    assert (df["is_stale"] == False).all()


def test_ticker_fails_after_snapshot_exists_falls_back_with_is_stale(monkeypatch, _isolated_snapshot):
    tickers = ["VOLV-B.ST"]

    # Seed a snapshot as if a prior successful run had written it.
    _mock_all_succeed(monkeypatch, tickers)
    yc.fetch_market_data(tickers)
    assert _isolated_snapshot.exists()

    def always_fail(url, headers=None, params=None, timeout=15):
        raise requests.ConnectionError("network is down")

    monkeypatch.setattr(requests, "get", always_fail)

    df = yc.fetch_market_data(tickers)

    validate(df, name="test")
    assert not df.empty
    assert (df["is_stale"] == True).all()
    assert set(df["metric_id"]) == set(tickers)


def test_all_tickers_fail_no_snapshot_returns_empty_frame(monkeypatch):
    def always_fail(url, headers=None, params=None, timeout=15):
        raise requests.ConnectionError("network is down")

    monkeypatch.setattr(requests, "get", always_fail)

    df = yc.fetch_market_data(["VOLV-B.ST", "ERIC-B.ST"])

    assert df.empty
    assert list(df.columns) == COMMON_COLUMNS


def test_fetch_market_data_market_closed_ticker_is_absent_not_stale(monkeypatch):
    # Market-closed (empty response) is a legitimate "no data", not a
    # failure - the ticker should just be absent, never flagged stale.
    def fake_get(url, headers=None, params=None, timeout=15):
        if "VOLV-B.ST" in url:
            return FakeResponse(_empty_chart_payload())
        return FakeResponse(_chart_payload(_DAILY_TIMESTAMPS, _DAILY_CLOSES))

    monkeypatch.setattr(requests, "get", fake_get)

    df = yc.fetch_market_data(["VOLV-B.ST", "ERIC-B.ST"])

    assert set(df["metric_id"]) == {"ERIC-B.ST"}
    assert (df["is_stale"] == False).all()


def test_fetch_market_data_all_tickers_subset_of_all_tickers(monkeypatch):
    _mock_all_succeed(monkeypatch, ALL_TICKERS)
    df = yc.fetch_market_data(ALL_TICKERS[:3])
    assert set(df["metric_id"]).issubset(set(ALL_TICKERS))
