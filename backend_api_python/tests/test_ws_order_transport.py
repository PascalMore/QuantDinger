from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlencode

import pytest

from app.services.live_trading import ws_order_transport as transport


class _CaptureConnection:
    def __init__(self, response):
        self.response = response
        self.payload = None
        self.matches = None
        self.terminal = None

    def request(self, payload, *, matches, terminal=None):
        self.payload = payload
        self.matches = matches
        self.terminal = terminal
        assert matches(self.response)
        return self.response


@pytest.fixture(autouse=True)
def _enable_ws(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_WS_ORDER_ENTRY", "true")


def test_binance_futures_ws_order_keeps_broker_client_id(monkeypatch):
    response = {"id": "unused", "status": 200, "result": {"orderId": 7}}
    capture = _CaptureConnection(response)

    def connection(**_kwargs):
        original = capture.request

        def request(payload, *, matches, terminal=None):
            response["id"] = payload["id"]
            return original(payload, matches=matches, terminal=terminal)

        capture.request = request
        return capture

    monkeypatch.setattr(transport, "_connection", connection)
    result = transport.submit_binance_order(
        api_key="key",
        secret_key="secret",
        params={
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "MARKET",
            "quantity": "0.01",
            "newClientOrderId": "x-HBpUbQjTorder-1",
            "timestamp": 123,
        },
        futures=True,
        demo=False,
    )
    assert result == {"orderId": 7}
    params = capture.payload["params"]
    assert params["apiKey"] == "key"
    assert params["newClientOrderId"] == "x-HBpUbQjTorder-1"
    unsigned = {key: value for key, value in params.items() if key != "signature"}
    expected = hmac.new(
        b"secret", urlencode(sorted(unsigned.items())).encode(), hashlib.sha256
    ).hexdigest()
    assert params["signature"] == expected


def test_okx_ws_order_keeps_broker_tag(monkeypatch):
    response = {
        "id": "unused",
        "code": "0",
        "msg": "",
        "data": [{"ordId": "9", "sCode": "0", "sMsg": ""}],
    }
    capture = _CaptureConnection(response)

    def connection(**_kwargs):
        original = capture.request

        def request(payload, *, matches, terminal=None):
            response["id"] = payload["id"]
            return original(payload, matches=matches, terminal=terminal)

        capture.request = request
        return capture

    monkeypatch.setattr(transport, "_connection", connection)
    result = transport.submit_okx_order(
        api_key="key",
        secret_key="secret",
        passphrase="pass",
        body={"instId": "BTC-USDT-SWAP", "tag": "56fa80b0ce8cBCDE"},
        simulated=False,
    )
    assert result["data"][0]["ordId"] == "9"
    assert capture.payload["args"][0]["tag"] == "56fa80b0ce8cBCDE"


def test_bybit_ws_order_sends_broker_referer(monkeypatch):
    response = {
        "reqId": "unused",
        "retCode": 0,
        "retMsg": "OK",
        "data": {"orderId": "11"},
        "header": {"Timenow": "1235"},
    }
    capture = _CaptureConnection(response)

    def connection(**_kwargs):
        original = capture.request

        def request(payload, *, matches, terminal=None):
            response["reqId"] = payload["reqId"]
            return original(payload, matches=matches, terminal=terminal)

        capture.request = request
        return capture

    monkeypatch.setattr(transport, "_connection", connection)
    result = transport.submit_bybit_order(
        api_key="key",
        secret_key="secret",
        body={"category": "linear", "symbol": "BTCUSDT"},
        base_url="https://api.bybit.com",
        recv_window_ms=12000,
        broker_referer="Ri001020",
        timestamp_ms=1234,
    )
    assert result["result"]["orderId"] == "11"
    assert capture.payload["header"] == {
        "X-BAPI-TIMESTAMP": "1234",
        "X-BAPI-RECV-WINDOW": "12000",
        "Referer": "Ri001020",
    }


def test_bitget_uta_ws_order_uses_v3_fields_and_broker_code(monkeypatch):
    response = {
        "id": "unused",
        "code": "0",
        "msg": "success",
        "ts": "1235",
        "args": [{"orderId": "12", "clientOid": "client-1"}],
    }
    capture = _CaptureConnection(response)

    def connection(**kwargs):
        assert kwargs["url"] == "wss://ws.bitget.com/v3/ws/private"
        assert kwargs["qualifier"] == "live:api-code"
        original = capture.request

        def request(payload, *, matches, terminal=None):
            response["id"] = payload["id"]
            return original(payload, matches=matches, terminal=terminal)

        capture.request = request
        return capture

    monkeypatch.setattr(transport, "_connection", connection)
    result = transport.submit_bitget_order(
        api_key="key",
        secret_key="secret",
        passphrase="pass",
        body={
            "category": "USDT-FUTURES",
            "symbol": "BTCUSDT",
            "side": "sell",
            "qty": "0.01",
            "reduceOnly": "yes",
            "takeProfit": "70000",
            "stopLoss": "60000",
            "clientOid": "client-1",
        },
        simulated=False,
        api_code="api-code",
    )

    assert result["data"] == {"orderId": "12", "clientOid": "client-1"}
    assert capture.payload["category"] == "usdt-futures"
    assert capture.payload["apiCode"] == "api-code"
    assert capture.payload["args"] == [
        {
            "symbol": "BTCUSDT",
            "side": "sell",
            "qty": "0.01",
            "reduceOnly": "YES",
            "takeprofit": "70000",
            "stoploss": "60000",
            "clientOid": "client-1",
        }
    ]


def test_gate_ws_order_sends_channel_id(monkeypatch):
    response = {
        "request_id": "unused",
        "ack": False,
        "header": {"status": "200"},
        "data": {"result": {"id": "13"}},
    }
    capture = _CaptureConnection(response)

    def connection(**_kwargs):
        original = capture.request

        def request(payload, *, matches, terminal=None):
            response["request_id"] = payload["payload"]["req_id"]
            return original(payload, matches=matches, terminal=terminal)

        capture.request = request
        return capture

    monkeypatch.setattr(transport, "_connection", connection)
    result = transport.submit_gate_order(
        api_key="key",
        secret_key="secret",
        body={"contract": "BTC_USDT", "size": "1", "price": "0", "tif": "ioc"},
        base_url="https://api.gateio.ws",
        futures=True,
        channel_id="dinger",
    )
    assert result["id"] == "13"
    assert capture.payload["payload"]["header"] == {"x-gate-channel-id": "dinger"}


def test_order_send_failure_is_unknown_not_safe_fallback():
    class BrokenSocket:
        def send(self, _payload):
            raise OSError("connection reset")

        def close(self):
            return None

    conn = transport._PersistentRpcSocket(url="wss://example.invalid")
    conn._socket = BrokenSocket()
    with pytest.raises(transport.WebSocketOrderUnknown):
        conn.request({"id": "1"}, matches=lambda _msg: True)


def test_heartbeat_keeps_idle_connection_warm(monkeypatch):
    real_float_env = transport._float_env
    monkeypatch.setattr(
        transport,
        "_float_env",
        lambda name, default, minimum, maximum: (
            0.01
            if name == "LIVE_TRADING_WS_ORDER_HEARTBEAT_SEC"
            else real_float_env(name, default, minimum, maximum)
        ),
    )

    class WarmSocket:
        def __init__(self):
            self.pings = 0

        def ping(self):
            self.pings += 1

        def close(self):
            return None

    conn = transport._PersistentRpcSocket(url="wss://example.invalid")
    sock = WarmSocket()
    conn._socket = sock
    conn._start_heartbeat()
    try:
        deadline = transport.time.monotonic() + 0.2
        while sock.pings == 0 and transport.time.monotonic() < deadline:
            transport.time.sleep(0.01)
        assert sock.pings >= 1
        assert conn.last_used > 0
    finally:
        conn.close()


def test_disabled_ws_order_entry_is_safe_noop(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_WS_ORDER_ENTRY", "false")
    monkeypatch.setattr(
        transport,
        "_connection",
        lambda **_kwargs: pytest.fail("connection must not be created when disabled"),
    )
    assert transport.submit_binance_order(
        api_key="key",
        secret_key="secret",
        params={},
        futures=False,
        demo=False,
    ) is None
