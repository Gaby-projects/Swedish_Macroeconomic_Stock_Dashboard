"""Mocked-HTTP tests for data.riksbank_client. No live network is used."""

from __future__ import annotations

import pandas as pd
import pytest
import requests

from swedish_macro_dashboard.constants import RIKSBANK_SERIES
from swedish_macro_dashboard.data import riksbank_client as rc
from swedish_macro_dashboard.data.schema import COMMON_COLUMNS, validate

POLICY_ROUND = "2026:2"


class FakeResponse:
    def __init__(self, json_data, status_code: int = 200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json_data


def _forecasts_payload(series_id: str, observations: list[dict], cutoff: str = "2025-01-01"):
    return {
        "data": [
            {
                "external_id": series_id,
                "metadata": {"unit": RIKSBANK_SERIES[series_id]["unit"]},
                "vintages": {
                    "metadata": {
                        "policy_round": POLICY_ROUND,
                        "forecast_cutoff_date": cutoff,
                    },
                    "observations": observations,
                },
            }
        ]
    }


def _default_observations(series_id: str) -> list[dict]:
    freq_dates = {
        "Q": ["2020-01-01", "2020-04-01", "2020-07-01", "2020-10-01", "2021-01-01"],
    }.get(RIKSBANK_SERIES[series_id]["frequency"], None)
    if freq_dates is None:
        freq_dates = ["2020-01-01", "2020-02-01", "2020-03-01", "2020-04-01", "2020-05-01"]
    return [{"dt": d, "value": 1.5 + i * 0.1} for i, d in enumerate(freq_dates)]


@pytest.fixture(autouse=True)
def _no_real_sleep():
    # Skip tenacity's actual backoff sleep so retry tests run instantly.
    rc._get_series_with_retry.retry.sleep = lambda seconds: None
    rc._get_latest_policy_round_with_retry.retry.sleep = lambda seconds: None


@pytest.fixture(autouse=True)
def _isolated_snapshot(tmp_path, monkeypatch):
    snap_dir = tmp_path / "data_snapshots"
    snap_file = snap_dir / "riksbank_macro.csv"
    monkeypatch.setattr(rc, "SNAPSHOT_DIR", snap_dir)
    monkeypatch.setattr(rc, "SNAPSHOT_FILE", snap_file)
    return snap_file


def _mock_all_succeed(monkeypatch, series_ids):
    def fake_get(url, timeout=15):
        if "policy_rounds" in url:
            return FakeResponse({"data": [POLICY_ROUND, "2026:1"]})
        for sid in series_ids:
            if f"series={sid}" in url:
                return FakeResponse(_forecasts_payload(sid, _default_observations(sid)))
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(requests, "get", fake_get)


def test_fetch_macro_series_success_is_clean_and_validated(monkeypatch):
    series_ids = ["SEQGDPNAYSA", "SEMCPIFNAYNA"]
    _mock_all_succeed(monkeypatch, series_ids)

    df = rc.fetch_macro_series(series_ids)

    validate(df, name="test")
    assert set(df["metric_id"]) == set(series_ids)
    assert (df["is_stale"] == False).all()
    assert df["date"].notna().all()
    assert df["value"].notna().all()
    assert not df.duplicated(subset=["date", "metric_id"]).any()
    assert pd.api.types.is_float_dtype(df["value"])
    assert pd.api.types.is_datetime64_any_dtype(df["date"])


def test_successful_fetch_writes_snapshot(monkeypatch, _isolated_snapshot):
    series_ids = ["SEQGDPNAYSA"]
    _mock_all_succeed(monkeypatch, series_ids)

    assert not _isolated_snapshot.exists()
    rc.fetch_macro_series(series_ids)
    assert _isolated_snapshot.exists()

    on_disk = pd.read_csv(_isolated_snapshot)
    assert set(on_disk["metric_id"]) == set(series_ids)


def test_dirty_response_is_deduped_and_coerced(monkeypatch):
    series_id = "SEMCPIFNAYNA"
    dirty_observations = [
        {"dt": "2020-01-01", "value": "1.5"},  # string numeric
        {"dt": "2020-01-01", "value": "1.6"},  # duplicate date, should keep last
        {"dt": "2020-02-01", "value": None},  # null value, should be dropped
        {"dt": "2020-03-01", "value": "2.0"},
    ]

    def fake_get(url, timeout=15):
        if "policy_rounds" in url:
            return FakeResponse({"data": [POLICY_ROUND]})
        return FakeResponse(_forecasts_payload(series_id, dirty_observations))

    monkeypatch.setattr(requests, "get", fake_get)

    df = rc.fetch_macro_series([series_id])

    validate(df, name="test")
    assert df["value"].notna().all()
    assert not df.duplicated(subset=["date", "metric_id"]).any()
    # duplicate 2020-01-01 rows collapse to one, the null 2020-02-01 row is dropped
    assert len(df) == 2
    kept = df.sort_values("date").reset_index(drop=True)
    assert kept.loc[0, "value"] == pytest.approx(1.6)


def test_all_series_fail_falls_back_to_snapshot(monkeypatch, _isolated_snapshot):
    series_ids = ["SEQGDPNAYSA", "SEMCPIFNAYNA"]

    # Seed a snapshot as if a prior successful run had written it.
    _mock_all_succeed(monkeypatch, series_ids)
    rc.fetch_macro_series(series_ids)
    assert _isolated_snapshot.exists()

    def always_fail(url, timeout=15):
        raise requests.ConnectionError("network is down")

    monkeypatch.setattr(requests, "get", always_fail)

    df = rc.fetch_macro_series(series_ids)

    validate(df, name="test")
    assert not df.empty
    assert (df["is_stale"] == True).all()
    assert set(df["metric_id"]) == set(series_ids)


def test_all_series_fail_no_snapshot_returns_empty_frame(monkeypatch):
    def always_fail(url, timeout=15):
        raise requests.ConnectionError("network is down")

    monkeypatch.setattr(requests, "get", always_fail)

    df = rc.fetch_macro_series(["SEQGDPNAYSA", "SEMCPIFNAYNA"])

    assert df.empty
    assert list(df.columns) == COMMON_COLUMNS


def test_one_series_failing_does_not_affect_others(monkeypatch, _isolated_snapshot):
    series_ids = ["SEQGDPNAYSA", "SEMCPIFNAYNA"]

    def fake_get(url, timeout=15):
        if "policy_rounds" in url:
            return FakeResponse({"data": [POLICY_ROUND]})
        if "series=SEMCPIFNAYNA" in url:
            raise requests.ConnectionError("boom")
        if "series=SEQGDPNAYSA" in url:
            return FakeResponse(
                _forecasts_payload("SEQGDPNAYSA", _default_observations("SEQGDPNAYSA"))
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    df = rc.fetch_macro_series(series_ids)

    # No snapshot exists for the failing series yet, so it's simply omitted;
    # the succeeding series is still returned live.
    assert set(df["metric_id"]) == {"SEQGDPNAYSA"}
    assert (df["is_stale"] == False).all()
