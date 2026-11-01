"""Agent contract tests for the training-OHLC klines mode via the real route.

DESIGN-06-002 V1.4 §11.7 / G02-01. Flask test client through the actual
``@agent_required(SCOPE_R)`` decorator with a fake token lookup and patched
audit persistence; the provider boundary is faked and every denial must keep
the provider-call count at zero.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from unittest import mock

import pytest

from app.routes.agent_v1 import markets as markets_route
from app.services.training_ohlc import normalize_training_payload
from app.utils import agent_auth


UTC = timezone.utc
CUTOFF = int(datetime(2026, 10, 31, tzinfo=UTC).timestamp())
OBSERVED = "2026-11-01T00:00:00Z"
TOKEN = "qd_agent_TESTTOKEN12345"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _rows(count: int) -> list[dict]:
    rows, price = [], 100.0
    begin = CUTOFF - count * 86400
    for index in range(count):
        rows.append({"t": begin + index * 86400, "o": str(price), "h": str(price + 5),
                     "l": str(price - 5), "c": str(price + 1), "v": "10", "sum": "20"})
        price += 1.0
    return rows


def _payload(count: int = 3) -> dict:
    return {
        "raw_rows": _rows(count),
        "metadata_raw": {"name": "PUMP_USDT", "type": "direct",
                         "base": "PUMP", "quote": "USDT", "settle": "USDT"},
        "metadata_retrieved_at_utc": OBSERVED,
        "request": {
            "host": "https://api.gateio.ws",
            "metadata_path": "/api/v4/futures/usdt/contracts",
            "path": "/api/v4/futures/usdt/candlesticks",
            "contract": "PUMP_USDT", "interval": "1d",
            "from": CUTOFF - count * 86400, "to": CUTOFF - 1,
            "limit": count, "before_time": CUTOFF,
        },
        "provider_request_count": 2,
        "exchange_id": "gate", "market_type": "swap",
    }


@pytest.fixture(autouse=True)
def _agent_env(monkeypatch):
    token = {
        "id": 7, "user_id": 1, "name": "training-agent", "scopes": "R",
        "markets": "*", "instruments": "*", "paper_only": True,
        "rate_limit_per_min": 600, "status": "active", "expires_at": None,
    }
    agent_auth._schema_ready = True
    agent_auth._rate_state.clear()
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda _: token)
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(markets_route, "_now_utc", lambda: datetime(2026, 10, 31, tzinfo=UTC))
    yield
    agent_auth._rate_state.clear()


@pytest.fixture()
def provider_spy(monkeypatch):
    """Fake the service I/O entry and count Native fetches (provider boundary)."""
    from app.services import training_ohlc as service

    calls: dict = {"fetch": 0}

    class FakeClient:
        id = "gate"
        market_type = "swap"

        def fetch_training_ohlc(self, symbol, *, before_time, limit):
            calls["fetch"] += 1
            calls["last"] = {"symbol": symbol, "before_time": before_time, "limit": limit}
            payload = _payload(limit)
            payload["request"]["from"] = before_time - limit * 86400
            payload["request"]["to"] = before_time - 1
            return payload

    monkeypatch.setattr(service, "create_native_crypto_client",
                        lambda *a, **k: FakeClient())
    return calls


# ------------------------------------------------- authorization before provider


def test_missing_token_is_401_with_zero_provider_calls(client, provider_spy):
    response = client.get(
        "/api/agent/v1/klines",
        query_string={"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
               "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
               "before_time": CUTOFF},
    )
    assert response.status_code == 401
    assert provider_spy["fetch"] == 0


def test_scope_market_and_instrument_denials_stop_before_provider(client, monkeypatch):
    agent_auth._rate_state.clear()
    token = {
        "id": 8, "user_id": 1, "name": "restricted", "scopes": "W",
        "markets": "USStock", "instruments": "AAPL", "paper_only": True,
        "rate_limit_per_min": 600, "status": "active", "expires_at": None,
    }
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda _: token)
    provider = {"fetch": 0}

    from app.services import training_ohlc as service

    class NoClient:
        def fetch_training_ohlc(self, *a, **k):
            provider["fetch"] += 1
            raise AssertionError("provider must not be reached")

    monkeypatch.setattr(service, "create_native_crypto_client",
                        lambda *a, **k: NoClient())

    base = {"data_mode": "training_ohlc", "timeframe": "1D", "exchange_id": "gate",
            "market_type": "swap", "before_time": CUTOFF}
    scope = client.get("/api/agent/v1/klines", headers=AUTH, query_string={**base, "market": "Crypto",
                                                      "symbol": "PUMPUSDT"})
    token["scopes"] = "R"
    market = client.get("/api/agent/v1/klines", headers=AUTH, query_string={**base, "market": "Crypto",
                                                       "symbol": "PUMPUSDT"})
    token["markets"] = "Crypto"
    instrument = client.get("/api/agent/v1/klines", headers=AUTH,
                            query_string={**base, "market": "Crypto", "symbol": "PUMPUSDT"})
    assert scope.status_code == 403
    assert market.status_code == 403
    assert instrument.status_code == 403
    assert provider["fetch"] == 0


# ------------------------------------------------------- strict request errors


@pytest.mark.parametrize("query", [
    # double cursor
    {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
     "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
     "before_time": CUTOFF, "beforeTime": CUTOFF},
    # future / non-midnight / junk before_time
    {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
     "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
     "before_time": CUTOFF + 86400},
    {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
     "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
     "before_time": CUTOFF + 1},
    {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
     "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
     "before_time": "not-a-number"},
    # missing identity fields / wrong venue / wrong product
    {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
     "timeframe": "1D", "before_time": CUTOFF},
    {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
     "timeframe": "1D", "exchange_id": "binance", "market_type": "swap",
     "before_time": CUTOFF},
    {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
     "timeframe": "1D", "exchange_id": "gate", "market_type": "spot",
     "before_time": CUTOFF},
    # bad timeframe / fractional or out-of-range limit / unknown mode
    {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
     "timeframe": "4H", "exchange_id": "gate", "market_type": "swap",
     "before_time": CUTOFF},
    {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
     "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
     "before_time": CUTOFF, "limit": "2.5"},
    {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
     "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
     "before_time": CUTOFF, "limit": "366"},
    {"data_mode": "kline", "market": "Crypto", "symbol": "PUMPUSDT"},
])
def test_invalid_training_requests_are_400_with_zero_provider_calls(client, provider_spy,
                                                                    query):
    response = client.get("/api/agent/v1/klines", headers=AUTH, query_string=query)
    assert response.status_code == 400
    body = response.get_json()
    assert body["code"] == 400
    assert body["message"] == "training.invalidRequest"
    assert body["data"] is None
    assert body["retriable"] is False
    assert provider_spy["fetch"] == 0


@pytest.mark.parametrize("mode", [None, "ohlcv"])
@pytest.mark.parametrize("identity", [
    {"exchange_id": "gate"}, {"market_type": "swap"},
    {"exchange_id": "gate", "market_type": "swap"},
    {"exchange_id": ""}, {"market_type": ""},
])
def test_legacy_identity_presence_is_invalid_before_any_provider(
        client, monkeypatch, provider_spy, mode, identity):
    legacy = mock.Mock(side_effect=AssertionError("legacy provider forbidden"))
    monkeypatch.setattr(markets_route._kline_service, "get_kline", legacy)
    query = {"market": "Crypto", "symbol": "PUMPUSDT", **identity}
    if mode is not None:
        query["data_mode"] = mode
    response = client.get("/api/agent/v1/klines", headers=AUTH, query_string=query)
    _assert_invalid(response)
    assert provider_spy["fetch"] == 0
    legacy.assert_not_called()


def _training_query():
    return {"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
            "exchange_id": "gate", "market_type": "swap", "timeframe": "1D",
            "before_time": str(CUTOFF), "limit": "365"}


def _assert_invalid(response):
    assert response.status_code == 400
    body = response.get_json()
    assert body["code"] == 400
    assert body["message"] == "training.invalidRequest"
    assert body["data"] is None
    assert body["retriable"] is False
    assert body["details"] == {"reason": "training.invalidRequest"}


@pytest.mark.parametrize("field", [
    "data_mode", "market", "symbol", "exchange_id", "market_type", "timeframe",
    "before_time", "beforeTime", "limit",
])
@pytest.mark.parametrize("same", [True, False])
def test_real_query_repeated_single_value_is_invalid(client, provider_spy, field, same):
    query = _training_query()
    if field == "beforeTime":
        query[field] = query.pop("before_time")
    repeated = list(query.items()) + [(field, query[field] if same else "other")]
    response = client.get("/api/agent/v1/klines", headers=AUTH, query_string=repeated)
    _assert_invalid(response)
    assert provider_spy["fetch"] == 0


@pytest.mark.parametrize("changes,missing", [
    ({"beforeTime": ""}, None), ({"before_time": "", "beforeTime": str(CUTOFF)}, None),
    ({"symbol": "PUMPUSDT@gate:swap"}, None),
    ({"symbol": "PUMP/USDT:USDC"}, None),
    ({}, "market"), ({}, "symbol"), ({"market": ""}, None), ({"symbol": ""}, None),
])
def test_real_query_alias_conflicts_and_missing_fields_are_input_errors(
        client, provider_spy, changes, missing):
    query = {**_training_query(), **changes}
    if missing:
        query.pop(missing)
    response = client.get("/api/agent/v1/klines", headers=AUTH, query_string=query)
    _assert_invalid(response)
    assert provider_spy["fetch"] == 0


# ------------------------------------------------------------- success + errors


def test_training_success_returns_frozen_envelope_and_forwards_request(
        client, provider_spy, monkeypatch):
    monkeypatch.setattr(markets_route, "_now_utc",
                        lambda: datetime(2026, 11, 1, tzinfo=UTC))
    response = client.get(
        "/api/agent/v1/klines",
        headers=AUTH, query_string={"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
               "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
               "before_time": CUTOFF},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["code"] == 0 and body["message"] == "ok"
    data = body["data"]
    assert data["data_mode"] == "training_ohlc"
    assert data["status"] == "ok" and data["count"] == 365
    assert data["synthetic"] is False
    assert data["volume_status"] == "not_provided"
    assert data["backtest_eligible"] is False
    assert data["historical_volume_validity"] == "UNKNOWN"
    assert data["identity"]["native_contract"] == "PUMP_USDT"
    assert provider_spy["fetch"] == 1
    assert provider_spy["last"] == {"symbol": "PUMPUSDT", "before_time": CUTOFF,
                                    "limit": 365}


def test_provider_source_failure_maps_to_502_source_unavailable(
        client, provider_spy, monkeypatch):
    from app.services import training_ohlc as service

    class Boom:
        def fetch_training_ohlc(self, symbol, *, before_time, limit):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(service, "create_native_crypto_client",
                        lambda *a, **k: Boom())
    response = client.get(
        "/api/agent/v1/klines",
        headers=AUTH, query_string={"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
               "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
               "before_time": CUTOFF},
    )
    assert response.status_code == 502
    body = response.get_json()
    assert body["message"] == "training.sourceUnavailable"
    assert body["data"] is None


def test_insufficient_evidence_is_http_200_code_0_with_explicit_status(
        client, provider_spy, monkeypatch):
    from app.services import training_ohlc as service

    payload = _payload(3)
    payload["raw_rows"] = _rows(2)  # trailing gap: last close < cutoff

    class ShortClient:
        def fetch_training_ohlc(self, symbol, *, before_time, limit):
            return payload

    monkeypatch.setattr(service, "create_native_crypto_client",
                        lambda *a, **k: ShortClient())
    response = client.get(
        "/api/agent/v1/klines",
        headers=AUTH, query_string={"data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
               "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
               "before_time": CUTOFF},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["code"] == 0
    assert body["data"]["status"] == "insufficient_evidence"


# ------------------------------------------------- legacy OHLCV stays untouched


def test_default_ohlcv_mode_matches_legacy_envelope_and_ignores_training_errors(
        client, monkeypatch):
    calls = []

    class FakeService:
        def get_kline(self, *, market, symbol, timeframe, limit, before_time):
            assert market == "Crypto" and symbol == "BTC/USDT"
            assert timeframe == "1D" and limit == 300
            calls.append(before_time)
            return [[1700000000000, 100.0, 110.0, 90.0, 105.0, 12.0]]

    monkeypatch.setattr(markets_route._kline_service, "get_kline",
                        FakeService().get_kline)
    response = client.get("/api/agent/v1/klines",
                          headers=AUTH, query_string={"market": "Crypto", "symbol": "BTC/USDT"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["code"] == 0
    assert body["data"] == {
        "market": "Crypto", "symbol": "BTC/USDT", "timeframe": "1D",
        "count": 1,
        "klines": [[1700000000000, 100.0, 110.0, 90.0, 105.0, 12.0]],
    }
    # Legacy before_time keeps working (single cursor, no training mode).
    legacy = client.get("/api/agent/v1/klines",
                        headers=AUTH, query_string={"market": "Crypto", "symbol": "BTC/USDT",
                               "before_time": 1700000000})
    assert legacy.status_code == 200
    assert calls == [None, 1700000000]


@pytest.mark.parametrize("mode", [None, "ohlcv"])
@pytest.mark.parametrize("failure,query,expected", [
    ("missing", {"market": "Crypto"}, (400, 400, "market and symbol are required", None, False)),
    ("cursor", {"market": "Crypto", "symbol": "BTC/USDT", "before_time": "invalid"},
     (400, 400, "before_time must be unix seconds", None, False)),
    ("provider", {"market": "Crypto", "symbol": "BTC/USDT"},
     (502, 500, "kline fetch failed", "offline legacy failure", True)),
])
def test_legacy_errors_are_unchanged_without_training_identity(
        client, monkeypatch, mode, failure, query, expected):
    legacy = mock.Mock(side_effect=RuntimeError("offline legacy failure"))
    monkeypatch.setattr(markets_route._kline_service, "get_kline", legacy)
    query = dict(query)
    if mode is not None:
        query["data_mode"] = mode
    response = client.get("/api/agent/v1/klines", headers=AUTH, query_string=query)
    http, code, message, details, retriable = expected
    assert response.status_code == http
    assert response.get_json() == {"code": code, "message": message, "details": details,
                                   "retriable": retriable, "data": None}
    assert legacy.call_count == (1 if failure == "provider" else 0)


@pytest.mark.parametrize("mode", [None, "ohlcv"])
@pytest.mark.parametrize("limit,expected", [(None, 300), ("0", 1), ("9999", 2000)])
def test_legacy_limits_and_cursor_alias_are_unchanged(client, monkeypatch, mode, limit, expected):
    legacy = mock.Mock(return_value=[])
    monkeypatch.setattr(markets_route._kline_service, "get_kline", legacy)
    query = {"market": "Crypto", "symbol": "BTC/USDT", "beforeTime": "1700000000"}
    if mode is not None:
        query["data_mode"] = mode
    if limit is not None:
        query["limit"] = limit
    response = client.get("/api/agent/v1/klines", headers=AUTH, query_string=query)
    assert response.status_code == 200
    assert response.get_json() == {"code": 0, "message": "ok", "data": {
        "market": "Crypto", "symbol": "BTC/USDT", "timeframe": "1D", "count": 0, "klines": []}}
    legacy.assert_called_once_with(market="Crypto", symbol="BTC/USDT", timeframe="1D",
                                   limit=expected, before_time=1700000000)


def _assert_openapi_value(value, schema, schemas):
    """Check every constraint used by the frozen training OpenAPI projection."""
    if "$ref" in schema:
        schema = schemas[schema["$ref"].rsplit("/", 1)[-1]]
    if value is None:
        assert schema.get("nullable") is True
        return
    kind = schema.get("type")
    if kind == "object":
        assert isinstance(value, dict)
        if "properties" in schema:
            assert set(schema["required"]) == set(schema["properties"])
            assert set(value) == set(schema["properties"])
            for field, field_schema in schema["properties"].items():
                _assert_openapi_value(value[field], field_schema, schemas)
    elif kind == "array":
        assert isinstance(value, list)
        for item in value:
            _assert_openapi_value(item, schema["items"], schemas)
    elif kind == "string":
        assert isinstance(value, str)
        if schema.get("format") == "date-time":
            assert datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo == UTC
    elif kind == "integer":
        assert type(value) is int
    elif kind == "number":
        assert type(value) in (float, int) and math.isfinite(value)
    elif kind == "boolean":
        assert type(value) is bool
    else:
        raise AssertionError(f"unhandled schema type: {kind}")
    if "enum" in schema:
        assert value in schema["enum"]
    if "minimum" in schema:
        assert value >= schema["minimum"]
    if "exclusiveMinimum" in schema:
        assert value > schema["exclusiveMinimum"]


@pytest.mark.parametrize("count", [365, 2, 0])
def test_real_training_route_matches_every_openapi_field(client, monkeypatch, count):
    from app.services import training_ohlc as service

    payload = _payload(365)
    payload["raw_rows"] = _rows(count)
    native = mock.Mock()
    native.fetch_training_ohlc.return_value = payload
    monkeypatch.setattr(service, "create_native_crypto_client", lambda *a, **k: native)
    response = client.get("/api/agent/v1/klines", headers=AUTH, query_string=_training_query())
    assert response.status_code == 200
    body = response.get_json()
    assert body["code"] == 0 and body["message"] == "ok"
    data = body["data"]
    document = Path(__file__).resolve().parents[2] / "docs/agent/agent-openapi.json"
    schemas = json.loads(document.read_text())["components"]["schemas"]
    schema = schemas["GridTrainingOhlcData"]
    _assert_openapi_value(data, schema, schemas)
    assert data["count"] == count
    assert data["status"] == ("ok" if count == 365 else "insufficient_evidence")
    assert set(data["source"]) == {
        "provider", "host", "path", "query", "retrieved_at_utc", "raw_rows", "raw_rows_sha256",
        "provider_request_count", "request_policy", "metadata"}
    assert data["identity_evidence"] == data["source"]["metadata"]
    assert set(data["coverage"]) == {
        "requested_start_utc", "cutoff_utc", "requested_limit", "raw_count", "actual_count",
        "unique_count", "first_open_utc", "last_open_utc", "last_close_utc", "duplicate_count",
        "conflicting_count", "missing_count", "misaligned_count", "excluded_unclosed_count",
        "excluded_out_of_range_count", "short_training_history", "listing_boundary"}
    assert data["coverage"]["listing_boundary"] == {
        "status": "UNKNOWN", "reason": "listing_boundary_unverified"}
    native.fetch_training_ohlc.assert_called_once_with("PUMPUSDT", before_time=CUTOFF, limit=365)
    # Negative controls: exact keys, nested enums and integer-vs-bool are enforced.
    for broken in (dict(data, count=True), dict(data, extra=True),
                   dict(data, identity=dict(data["identity"], market_type="spot"))):
        with pytest.raises(AssertionError):
            _assert_openapi_value(broken, schema, schemas)


def test_service_payload_projection_is_shared_with_route(client):
    """The route envelope carries the same normalized projection as the service."""
    data = normalize_training_payload(_payload(3), {
        "data_mode": "training_ohlc", "market": "Crypto", "symbol": "PUMPUSDT",
        "timeframe": "1D", "exchange_id": "gate", "market_type": "swap",
        "before_time": CUTOFF, "limit": 3,
    }, observed_at_utc=OBSERVED)
    assert set(data) >= {
        "schema_version", "data_mode", "status", "reason", "market", "symbol",
        "timeframe", "count", "identity", "identity_evidence", "source", "klines",
        "normalized_sha256", "coverage", "synthetic", "volume_status",
        "backtest_eligible", "historical_volume_validity",
    }


@pytest.fixture(scope="session", autouse=True)
def _offline_metadata_route_session():
    """Guard app startup and transport before constructing the real Flask client."""
    import contextlib

    with contextlib.ExitStack() as guards:
        for target in ("app._bootstrap_database", "app.run_startup_hooks"):
            guards.enter_context(mock.patch(target))
        for target in ("socket.socket.connect", "socket.socket.connect_ex",
                       "socket.create_connection", "urllib.request.urlopen",
                       "requests.sessions.Session.request"):
            guards.enter_context(mock.patch(
                target, side_effect=AssertionError("offline metadata I/O forbidden")))
        yield


@pytest.fixture()
def native_metadata_transport(monkeypatch):
    """Keep Native creation/fetch and normalization real; fake only transport."""
    from app.data_sources.native_crypto import NativeCryptoPublicClient
    from app.utils.cache import CacheManager

    state = {"metadata": dict(_payload()["metadata_raw"]), "calls": []}
    state["metadata"]["provider_private"] = "F06_PRIVATE_METADATA_SENTINEL"

    def fake_payload(native, url, params=None):
        assert native.id == "gate" and native.market_type == "swap"
        state["calls"].append((url, params))
        if url == "https://api.gateio.ws/api/v4/futures/usdt/contracts":
            assert params is None
            return [state["metadata"]]
        assert url == "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
        assert params == {"contract": "PUMP_USDT", "interval": "1d",
                          "from": CUTOFF - 365 * 86400, "to": CUTOFF - 1}
        return _rows(365)

    monkeypatch.setattr(NativeCryptoPublicClient, "_get_training_payload", fake_payload)
    forbidden = []
    for owner, name in ((NativeCryptoPublicClient, "_get"),
                        (NativeCryptoPublicClient, "fetch_ohlcv"),
                        (CacheManager, "__init__"), (CacheManager, "get"),
                        (CacheManager, "set"), (CacheManager, "delete")):
        guard = mock.Mock(side_effect=AssertionError(f"metadata path entered {name}"))
        monkeypatch.setattr(owner, name, guard)
        forbidden.append(guard)
    yield state
    for guard in forbidden:
        guard.assert_not_called()


@pytest.mark.parametrize("field,value,missing", [
    pytest.param("name", None, True, id="missing-name"),
    pytest.param("type", None, True, id="missing-type"),
    pytest.param("name", None, False, id="null-name"),
    pytest.param("type", None, False, id="null-type"),
    pytest.param("name", 42, False, id="nonstring-name"),
    pytest.param("type", 42, False, id="nonstring-type"),
    pytest.param("name", "", False, id="empty-name"),
    pytest.param("type", "", False, id="empty-type"),
    pytest.param("name", "OTHER_USDT_F06_RAW_SENTINEL", False, id="wrong-name"),
    pytest.param("type", "inverse", False, id="inverse-type"),
    pytest.param("type", "spot", False, id="spot-type"),
    pytest.param("base", "OTHER_F06_RAW_SENTINEL", False, id="wrong-base"),
    pytest.param("quote", "USDC_F06_RAW_SENTINEL", False, id="quote-conflict"),
    pytest.param("settle", "USDC_F06_RAW_SENTINEL", False, id="settle-conflict"),
])
def test_real_native_metadata_identity_errors_are_controlled_502(
        client, native_metadata_transport, field, value, missing):
    metadata = native_metadata_transport["metadata"]
    if missing:
        metadata.pop(field)
    else:
        metadata[field] = value
    response = client.get("/api/agent/v1/klines", headers=AUTH, query_string=_training_query())
    assert response.status_code == 502
    assert response.get_json() == {
        "code": 502, "message": "training.identityUnverified", "data": None,
        "retriable": False, "details": {"reason": "training.identityUnverified"},
    }
    text = response.get_data(as_text=True)
    for forbidden in ("F06_PRIVATE_METADATA_SENTINEL", "F06_RAW_SENTINEL", TOKEN,
                      "KeyError", "Traceback", "metadata_raw", "raw_rows"):
        assert forbidden not in text
    calls = native_metadata_transport["calls"]
    assert calls[0] == ("https://api.gateio.ws/api/v4/futures/usdt/contracts", None)
    assert 1 <= len(calls) <= 2


@pytest.mark.parametrize("missing", [
    pytest.param((), id="normal-control"),
    pytest.param(("quote",), id="optional-quote-absent"),
    pytest.param(("settle",), id="optional-settle-absent"),
    pytest.param(("quote", "settle"), id="optional-quote-settle-absent"),
])
def test_real_native_metadata_optional_fields_keep_path_contract_provenance(
        client, native_metadata_transport, missing):
    metadata = native_metadata_transport["metadata"]
    for field in missing:
        metadata.pop(field)
    response = client.get("/api/agent/v1/klines", headers=AUTH, query_string=_training_query())
    assert response.status_code == 200
    body = response.get_json()
    assert body["code"] == 0 and body["message"] == "ok"
    data = body["data"]
    assert data["status"] == "ok" and data["reason"] is None and data["count"] == 365
    assert data["identity"] == {
        "exchange_id": "gate", "market_type": "swap", "settle": "USDT",
        "contract_type": "direct", "canonical_symbol": "PUMP/USDT:USDT",
        "native_contract": "PUMP_USDT",
    }
    evidence = data["identity_evidence"]
    assert evidence == data["source"]["metadata"]
    assert evidence["raw"] == {key: value for key, value in metadata.items()
                               if key != "provider_private"}
    assert evidence["source"] == {
        "host": "https://api.gateio.ws", "path": "/api/v4/futures/usdt/contracts"}
    assert evidence["settlement_basis"] == "endpoint_path"
    assert evidence["quote_basis"] == "contract_name"
    assert evidence["metadata_scope"] == "current_snapshot"
    assert evidence["historical_identity_validity"] == "UNKNOWN"
    assert evidence["retrieved_at_utc"].endswith("Z")
    assert "F06_PRIVATE_METADATA_SENTINEL" not in response.get_data(as_text=True)
    assert len(native_metadata_transport["calls"]) == data["source"]["provider_request_count"] == 2
