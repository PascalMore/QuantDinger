"""Offline training-OHLC tests: Native entry, service purity, request identity.

DESIGN-06-002 V1.4 §11.7 / G02-02..04. No network, no cache, no credentials:
the provider boundary is patched, the identity fixtures are synthetic raw
Gate objects, and every digest is recomputed client-side per SPEC §9.3.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import datetime, timezone
from unittest import mock

import pytest


@pytest.fixture(scope="session", autouse=True)
def _offline_training_session():
    """Guard shared app startup before any session app fixture is constructed."""
    with contextlib.ExitStack() as guards:
        guards.enter_context(mock.patch("app._bootstrap_database"))
        guards.enter_context(mock.patch("app.run_startup_hooks"))
        for target in ("socket.socket.connect", "socket.socket.connect_ex",
                       "socket.create_connection", "urllib.request.urlopen",
                       "requests.sessions.Session.request"):
            guards.enter_context(mock.patch(target, side_effect=AssertionError("offline I/O forbidden")))
        yield

from app.data_sources.native_crypto import (
    NativeCryptoAPIError,
    NativeCryptoPublicClient,
)
from app.services.training_ohlc import (
    TrainingIdentityError,
    TrainingRequestError,
    TrainingSourceError,
    canonical_bytes,
    get_training_ohlc,
    normalize_training_payload,
    parse_training_request,
    sha256_hex,
)


UTC = timezone.utc
CUTOFF = int(datetime(2026, 10, 31, tzinfo=UTC).timestamp())
OBSERVED = "2026-11-01T00:00:00Z"


def _raw_rows(count: int, *, start_day: int | None = None) -> list[dict]:
    begin = CUTOFF - count * 86400 if start_day is None else start_day
    rows = []
    price = 100.0
    for index in range(count):
        t = begin + index * 86400
        rows.append({
            "t": t, "o": str(price), "h": str(price + 5), "l": str(price - 5),
            "c": str(price + 1), "v": "1234.5", "sum": "9999.9",
        })
        price += 1.0
    return rows


def _payload(count: int = 365, **overrides) -> dict:
    payload = {
        "raw_rows": _raw_rows(count),
        "metadata_raw": {"name": "PUMP_USDT", "type": "direct",
                         "base": "PUMP", "quote": "USDT", "settle": "USDT"},
        "metadata_retrieved_at_utc": "2026-11-01T00:00:00Z",
        "request": {
            "host": "https://api.gateio.ws",
            "metadata_path": "/api/v4/futures/usdt/contracts",
            "path": "/api/v4/futures/usdt/candlesticks",
            "contract": "PUMP_USDT",
            "interval": "1d",
            "from": CUTOFF - 365 * 86400,
            "to": CUTOFF - 1,
            "limit": 365,
            "before_time": CUTOFF,
        },
        "provider_request_count": 2,
        "exchange_id": "gate",
        "market_type": "swap",
    }
    payload.update(overrides)
    return payload


def _request(limit: int = 365) -> dict:
    return {
        "data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
        "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
        "before_time": CUTOFF, "limit": limit,
    }


def _full(count: int = 365):
    return normalize_training_payload(_payload(count), _request(), observed_at_utc=OBSERVED)


# --------------------------------------------------------------- strict parsing


def test_parse_training_request_full_valid_shape():
    parsed = parse_training_request(
        {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
         "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
         "before_time": str(CUTOFF)},
        now_utc=datetime(2026, 11, 1, tzinfo=UTC),
    )
    assert parsed == {
        "data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
        "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
        "before_time": CUTOFF, "limit": 365,
    }


def test_parse_default_without_data_mode_is_ohlcv_passthrough():
    parsed = parse_training_request(
        {"market": "Crypto", "symbol": "BTC/USDT"}, now_utc=datetime(2026, 11, 1, tzinfo=UTC))
    assert parsed == {"data_mode": "ohlcv"}


@pytest.mark.parametrize("changes", [
    {"market": "USStock"}, {"exchange_id": "binance"}, {"market_type": "spot"},
    {"timeframe": "4H"}, {"data_mode": "websocket"},
])
def test_parse_rejects_wrong_identity_market_or_mode(changes):
    args = {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
            "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
            "before_time": str(CUTOFF)}
    args.update(changes)
    with pytest.raises(TrainingRequestError):
        parse_training_request(args, now_utc=datetime(2026, 11, 1, tzinfo=UTC))


def test_parse_rejects_double_cursor_and_conflicting_duplicates():
    base = {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
            "timeframe": "1D", "exchange_id": "gate", "market_type": "swap"}
    with pytest.raises(TrainingRequestError):  # both cursors
        parse_training_request({**base, "before_time": str(CUTOFF),
                                "beforeTime": str(CUTOFF)},
                               now_utc=datetime(2026, 11, 1, tzinfo=UTC))
    with pytest.raises(TrainingRequestError):  # repeated conflicting single value
        parse_training_request({**base, "before_time": [str(CUTOFF), str(CUTOFF - 86400)]},
                               now_utc=datetime(2026, 11, 1, tzinfo=UTC))


@pytest.mark.parametrize("bad", [
    str(CUTOFF + 86400),  # future
    str(CUTOFF + 3600),   # not midnight
    "1.5", "true", "", "  ", "-5",
])
def test_parse_rejects_future_non_midnight_and_junk_before_time(bad):
    with pytest.raises(TrainingRequestError):
        parse_training_request(
            {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
             "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
             "before_time": bad},
            now_utc=datetime(2026, 10, 31, tzinfo=UTC))


@pytest.mark.parametrize("bad", ["1", "366", "2.5", "abc", "true", "0"])
def test_parse_rejects_out_of_range_or_fractional_limit_without_clamping(bad):
    with pytest.raises(TrainingRequestError):
        parse_training_request(
            {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
             "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
             "before_time": str(CUTOFF), "limit": bad},
            now_utc=datetime(2026, 11, 1, tzinfo=UTC))


def test_parse_accepts_limit_boundaries_2_and_365():
    base = {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
            "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
            "before_time": str(CUTOFF)}
    assert parse_training_request({**base, "limit": "2"},
                                  now_utc=datetime(2026, 11, 1, tzinfo=UTC))["limit"] == 2
    assert parse_training_request({**base, "limit": "365"},
                                  now_utc=datetime(2026, 11, 1, tzinfo=UTC))["limit"] == 365


def test_ohlcv_mode_rejects_training_identity_params():
    with pytest.raises(TrainingRequestError):
        parse_training_request(
            {"data_mode": "ohlcv", "market": "Crypto", "symbol": "BTC/USDT",
             "exchange_id": "gate", "market_type": "swap"},
            now_utc=datetime(2026, 11, 1, tzinfo=UTC))


# --------------------------------------------------------------- native entry


def _patch_transport(monkeypatch, client, calls, *, contracts=None, rows=None):
    import requests

    def fake_get(url, params=None, **_kwargs):
        calls.append((url, dict(params or {})))
        assert _kwargs.get("allow_redirects") is False
        if "contracts" in url:
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(
                contracts if contracts is not None else [{
                    "name": "PUMP_USDT", "type": "direct", "base": "PUMP",
                    "quote": "USDT", "settle": "USDT"}]).encode("utf-8")
            return response
        payload = rows if rows is not None else _raw_rows(365)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(payload).encode("utf-8")
        return response

    monkeypatch.setattr(client, "_http_session",
                        lambda: type("S", (), {"get": staticmethod(fake_get)})())
    monkeypatch.setattr(client, "_throttle", lambda: None)


def test_native_fetch_training_ohlc_sends_two_bounded_gets(monkeypatch):
    client = NativeCryptoPublicClient("gate", "swap")
    calls: list[tuple[str, dict]] = []
    _patch_transport(monkeypatch, client, calls)
    payload = client.fetch_training_ohlc("PUMPUSDT", before_time=CUTOFF, limit=365)
    assert len(calls) == 2
    metadata_url, candle_url = calls
    assert metadata_url[0] == "https://api.gateio.ws/api/v4/futures/usdt/contracts"
    assert candle_url[0] == "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
    assert candle_url[1] == {
        "contract": "PUMP_USDT", "interval": "1d",
        "from": CUTOFF - 365 * 86400, "to": CUTOFF - 1,
    }
    assert "limit" not in candle_url[1]
    assert payload["provider_request_count"] == 2
    assert payload["metadata_raw"]["name"] == "PUMP_USDT"
    # Raw rows stay untouched: original values and types preserved.
    assert payload["raw_rows"][0]["t"] == CUTOFF - 365 * 86400
    assert isinstance(payload["raw_rows"][0]["o"], str)


@pytest.mark.parametrize("base", ["PUMP", "AAVE", "JUP", "SUI"])
def test_same_gate_fixture_training_isolated_from_legacy_unverified_volume(base, monkeypatch):
    from app.data_sources.crypto import CryptoDataSource
    from app.services import training_ohlc as service
    from app.services.kline import KlineService
    from app.utils.cache import CacheManager

    native = NativeCryptoPublicClient("gate", "swap")
    rows = _raw_rows(365)
    metadata = {"name": f"{base}_USDT", "type": "direct", "base": base,
                "quote": "USDT", "settle": "USDT", "quanto_multiplier": "0.01"}
    calls = []
    _patch_transport(monkeypatch, native, calls, contracts=[metadata], rows=rows)
    monkeypatch.setattr(service, "create_native_crypto_client", lambda *a, **k: native)
    with contextlib.ExitStack() as guards:
        forbidden = [guards.enter_context(mock.patch.object(owner, name,
                     side_effect=AssertionError(f"training entered {name}")))
                     for owner, name in (
                         (KlineService, "get_kline"),
                         (CryptoDataSource, "get_kline"),
                         (CryptoDataSource, "_fetch_ohlcv"),
                         (CryptoDataSource, "_fetch_ohlcv_fallback"),
                         (CryptoDataSource, "format_kline"),
                         (CacheManager, "__init__"), (CacheManager, "get"),
                         (CacheManager, "set"), (CacheManager, "delete"))]
        parser = guards.enter_context(mock.patch.object(native, "_parse_gate_swap_ohlcv",
                                                       wraps=native._parse_gate_swap_ohlcv))
        old_get = guards.enter_context(mock.patch.object(native, "_get", return_value=rows))
        data = get_training_ohlc(symbol=f"{base}USDT", before_time=CUTOFF)
        assert data["status"] == "ok" and data["count"] == 365
        assert data["source"]["raw_rows"] == rows
        assert data["source"]["metadata"]["raw"] == {
            key: value for key, value in metadata.items() if key != "quanto_multiplier"}
        assert data["identity"]["native_contract"] == f"{base}_USDT"
        assert data["coverage"]["listing_boundary"]["status"] == "UNKNOWN"
        for bar in data["klines"]:
            assert set(bar) == {"open_time_utc", "close_time_utc", "available_at_utc",
                                "availability_basis", "observed_at_utc", "open", "high",
                                "low", "close", "identity"}
        assert data["volume_status"] == "not_provided"
        assert data["backtest_eligible"] is False
        parser.assert_not_called()
        old_get.assert_not_called()
        for guard in forbidden:
            guard.assert_not_called()
        assert len(calls) == 2
        # The exact same rows and current metadata enter the old public entry.
        # No private synthetic metadata argument is supplied by production.
        native.markets = {f"{base}/USDT:USDT": {
            "id": f"{base}_USDT", "settle": "USDT", "quote": "USDT", "info": metadata}}
        with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeHistoricalUnverified"):
            native.fetch_ohlcv(f"{base}/USDT:USDT", "1d", limit=365)
        parser.assert_called_once()
        assert parser.call_args.args[0] is rows
        assert "metadata" not in parser.call_args.kwargs
        old_get.assert_called_once()
        assert len(calls) == 2


def test_native_rejects_non_gate_instance_and_alias_symbols(monkeypatch):
    spot = NativeCryptoPublicClient("gate", "spot")
    with pytest.raises(NativeCryptoAPIError):
        spot.fetch_training_ohlc("PUMPUSDT", before_time=CUTOFF)
    other = NativeCryptoPublicClient("binance", "swap")
    with pytest.raises(NativeCryptoAPIError):
        other.fetch_training_ohlc("BTCUSDT", before_time=CUTOFF)
    client = NativeCryptoPublicClient("gate", "swap")
    calls: list = []
    _patch_transport(monkeypatch, client, calls)
    for bad in ("PUMPUSDT@gate:swap", "PUMP/USDT:USDC", "BTCUSD", ""):
        with pytest.raises(NativeCryptoAPIError):
            client.fetch_training_ohlc(bad, before_time=CUTOFF)
    assert calls == []  # nothing leaked to the provider before identity checks


def test_native_rejects_duplicate_or_missing_contract(monkeypatch):
    client = NativeCryptoPublicClient("gate", "swap")
    calls: list = []
    _patch_transport(monkeypatch, client, calls,
                     contracts=[{"name": "PUMP_USDT", "type": "direct"},
                                {"name": "PUMP_USDT", "type": "direct"}])
    with pytest.raises(NativeCryptoAPIError, match="identityUnverified"):
        client.fetch_training_ohlc("PUMPUSDT", before_time=CUTOFF)
    assert len(calls) == 1  # candlesticks never sent after identity failure


def test_native_rejects_non_2xx_and_non_list_payload(monkeypatch):
    import requests

    client = NativeCryptoPublicClient("gate", "swap")

    class Boom(Exception):
        pass

    def fail_get(url, params=None, **kwargs):
        response = requests.Response()
        response.status_code = 503
        raise Boom()

    monkeypatch.setattr(client, "_http_session",
                        lambda: type("S", (), {"get": staticmethod(fail_get)})())
    monkeypatch.setattr(client, "_throttle", lambda: None)
    with pytest.raises(NativeCryptoAPIError, match="sourceUnavailable"):
        client.fetch_training_ohlc("PUMPUSDT", before_time=CUTOFF)

    calls: list = []
    _patch_transport(monkeypatch, client, calls, rows={"not": "a list"})
    with pytest.raises(NativeCryptoAPIError, match="sourceUnavailable"):
        client.fetch_training_ohlc("PUMPUSDT", before_time=CUTOFF)


# --------------------------------------------------------------- normalization


def test_normalize_full_year_success_and_client_side_digests():
    data = _full()
    assert data["status"] == "ok" and data["count"] == 365
    assert data["schema_version"] == "grid_training_ohlc_v1"
    assert data["data_mode"] == "training_ohlc"
    assert data["identity"] == {
        "exchange_id": "gate", "market_type": "swap", "settle": "USDT",
        "contract_type": "direct", "canonical_symbol": "PUMP/USDT:USDT",
        "native_contract": "PUMP_USDT",
    }
    assert data["synthetic"] is False
    assert data["volume_status"] == "not_provided"
    assert data["backtest_eligible"] is False
    assert data["historical_volume_validity"] == "UNKNOWN"
    assert data["coverage"]["short_training_history"] is False
    assert data["coverage"]["listing_boundary"] == {
        "status": "UNKNOWN", "reason": "listing_boundary_unverified"}
    # Client-side recomputation over the exact persisted objects.
    payload = _payload()
    assert data["source"]["raw_rows_sha256"] == hashlib.sha256(
        canonical_bytes(payload["raw_rows"])).hexdigest()
    assert data["source"]["metadata"]["sha256"] == sha256_hex(payload["metadata_raw"])
    assert data["normalized_sha256"] == sha256_hex(data["klines"])
    bar = data["klines"][-1]
    assert bar["open_time_utc"] == "2026-10-30T00:00:00Z"
    assert bar["close_time_utc"] == "2026-10-31T00:00:00Z"
    assert bar["available_at_utc"] == bar["close_time_utc"]
    assert bar["availability_basis"] == "bar_close_boundary"
    assert bar["observed_at_utc"] == OBSERVED
    assert "volume" not in bar and "base_volume" not in bar
    assert data["source"]["request_policy"] == {
        "automatic_retries": 0, "pagination": 0, "redirects": 0}


def test_normalize_preserves_raw_order_and_tampered_digest_is_detectable():
    payload = _payload(3)
    data = normalize_training_payload(payload, _request(3), observed_at_utc=OBSERVED)
    recorded = data["source"]["raw_rows"]
    assert recorded == payload["raw_rows"]  # original order preserved
    # Flipping a raw value changes the digest -> client recomputation detects it.
    tampered = [dict(row) for row in payload["raw_rows"]]
    tampered[0]["o"] = "999"
    assert sha256_hex(tampered) != data["source"]["raw_rows_sha256"]


def test_normalize_metadata_tampering_breaks_metadata_digest():
    payload = _payload(3)
    data = normalize_training_payload(payload, _request(3), observed_at_utc=OBSERVED)
    tampered = dict(payload["metadata_raw"], type="spot")
    assert sha256_hex(tampered) != data["source"]["metadata"]["sha256"]


def test_normalize_short_return_without_listing_proof_is_unknown_not_true():
    data = _full(364)
    assert data["status"] == "insufficient_evidence"
    assert data["coverage"]["short_training_history"] is None
    assert data["coverage"]["listing_boundary"]["status"] == "UNKNOWN"


def test_normalize_trailing_gap_rejected():
    payload = _payload(365)
    payload["raw_rows"] = _raw_rows(365)[:-1]  # last close = CUTOFF-1d, not CUTOFF
    data = normalize_training_payload(payload, _request(), observed_at_utc=OBSERVED)
    assert data["status"] == "insufficient_evidence"
    assert "trailingGap" in data["reason"]


def test_normalize_mid_window_gap_rejected_with_counts():
    payload = _payload(365)
    rows = _raw_rows(365)
    del rows[100]
    payload["raw_rows"] = rows
    data = normalize_training_payload(payload, _request(), observed_at_utc=OBSERVED)
    assert data["status"] == "insufficient_evidence"
    assert data["coverage"]["missing_count"] == 1


def test_normalize_identical_duplicate_is_not_ready_but_counted():
    payload = _payload(365)
    rows = _raw_rows(365)
    rows.append(dict(rows[10]))  # identical duplicate, same values
    payload["raw_rows"] = rows
    data = normalize_training_payload(payload, _request(), observed_at_utc=OBSERVED)
    assert data["status"] == "insufficient_evidence"
    assert data["coverage"]["duplicate_count"] == 1
    assert data["coverage"]["conflicting_count"] == 0


def test_normalize_conflicting_duplicate_counted():
    payload = _payload(365)
    rows = _raw_rows(365)
    rows.append(dict(rows[10], c="109"))  # conflicting but still within low/high
    payload["raw_rows"] = rows
    data = normalize_training_payload(payload, _request(), observed_at_utc=OBSERVED)
    assert data["status"] == "insufficient_evidence"
    assert data["coverage"]["duplicate_count"] == 1
    assert data["coverage"]["conflicting_count"] == 1


def test_normalize_misaligned_open_rejected():
    payload = _payload(365)
    rows = _raw_rows(365)
    rows[50] = dict(rows[50], t=rows[50]["t"] + 3600)  # not a midnight
    payload["raw_rows"] = rows
    data = normalize_training_payload(payload, _request(), observed_at_utc=OBSERVED)
    assert data["status"] == "insufficient_evidence"


@pytest.mark.parametrize("field,value", [
    ("o", "NaN"), ("o", "abc"), ("h", "-1"), ("l", "0"), ("c", ""),
])
def test_normalize_string_nan_or_illegal_price_is_insufficient_evidence(field, value):
    payload = _payload(365)
    rows = _raw_rows(365)
    rows[7][field] = value
    payload["raw_rows"] = rows
    data = normalize_training_payload(payload, _request(), observed_at_utc=OBSERVED)
    assert data["status"] == "insufficient_evidence"
    assert rows[7][field] == value  # raw evidence still preserved


def test_normalize_nonfinite_number_payload_is_structural_error():
    payload = _payload(3)
    payload["raw_rows"] = [
        {"t": CUTOFF - 3 * 86400, "o": float("nan"), "h": "10", "l": "9", "c": "9.5"}]
    with pytest.raises(TrainingSourceError):
        normalize_training_payload(payload, _request(3), observed_at_utc=OBSERVED)


def test_normalize_missing_identity_fields_raise_identity_error():
    payload = _payload(3)
    payload["metadata_raw"] = {"name": "PUMP_USDT"}  # type missing
    with pytest.raises(TrainingIdentityError):
        normalize_training_payload(payload, _request(3), observed_at_utc=OBSERVED)
    payload = _payload(3)
    payload["metadata_raw"] = {"name": "PUMP_USDT", "type": "spot"}
    with pytest.raises(TrainingIdentityError):
        normalize_training_payload(payload, _request(3), observed_at_utc=OBSERVED)
    payload = _payload(3)
    payload["metadata_raw"] = {"name": "OTHER_USDT", "type": "direct"}
    with pytest.raises(TrainingIdentityError):
        normalize_training_payload(payload, _request(3), observed_at_utc=OBSERVED)


def test_normalize_empty_rows_keeps_provenance_and_is_insufficient():
    payload = _payload(0)
    payload["raw_rows"] = []
    data = normalize_training_payload(payload, _request(), observed_at_utc=OBSERVED)
    assert data["status"] == "insufficient_evidence"
    assert data["count"] == 0
    assert data["source"]["raw_rows"] == []
    assert data["coverage"]["raw_count"] == 0


def test_normalize_out_of_range_rows_excluded_and_counted():
    payload = _payload(365)
    rows = _raw_rows(365) + [{"t": CUTOFF + 86400, "o": "1", "h": "2", "l": "0.5", "c": "1.5"}]
    payload["raw_rows"] = rows
    data = normalize_training_payload(payload, _request(), observed_at_utc=OBSERVED)
    assert data["status"] == "ok"
    assert data["coverage"]["excluded_out_of_range_count"] == 1
    assert data["count"] == 365


# --------------------------------------------------------------- service I/O


def test_get_training_ohlc_uses_fresh_client_and_maps_errors(monkeypatch):
    from app.services import training_ohlc as service

    created = []

    class FakeClient:
        id = "gate"
        market_type = "swap"

        def __init__(self):
            created.append(self)

        def fetch_training_ohlc(self, symbol, *, before_time, limit):
            return _payload(3)

    monkeypatch.setattr(service, "create_native_crypto_client",
                        lambda *a, **k: FakeClient())
    data = get_training_ohlc(symbol="PUMPUSDT", before_time=CUTOFF, limit=3)
    assert len(created) == 1  # fresh client per call
    assert data["status"] == "ok"

    def identity_boom(symbol, *, before_time, limit):
        raise NativeCryptoAPIError("training.identityUnverified: x")

    monkeypatch.setattr(service, "create_native_crypto_client",
                        lambda *a, **k: type("C", (), {"fetch_training_ohlc": staticmethod(identity_boom)})())
    with pytest.raises(TrainingIdentityError):
        get_training_ohlc(symbol="PUMPUSDT", before_time=CUTOFF, limit=3)

    def source_boom(symbol, *, before_time, limit):
        raise NativeCryptoAPIError("training.sourceUnavailable: x")

    monkeypatch.setattr(service, "create_native_crypto_client",
                        lambda *a, **k: type("C", (), {"fetch_training_ohlc": staticmethod(source_boom)})())
    with pytest.raises(TrainingSourceError):
        get_training_ohlc(symbol="PUMPUSDT", before_time=CUTOFF, limit=3)
