import inspect
from contextlib import contextmanager
from datetime import datetime

from flask import Flask, g

from app.routes import quick_trade


class _Cursor:
    def __init__(self):
        self.query = ""
        self.params = ()

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchall(self):
        return [{
            "id": 7,
            "credential_id": 305,
            "exchange_id": "okx",
            "symbol": "BTC/USDT",
            "side": "buy",
            "order_type": "market",
            "amount": 100,
            "price": 0,
            "leverage": 5,
            "market_type": "swap",
            "status": "filled",
            "exchange_order_id": "order-1",
            "filled_amount": 0.001,
            "avg_fill_price": 75000,
            "commission": 0.03,
            "commission_ccy": "USDT",
            "commission_quote": 0.03,
            "realized_pnl": 12.5,
            "raw_result": {
                "_quick_trade": {
                    "margin_mode": "cross",
                    "requested_base_qty": 0.0012,
                    "notional_usdt": 500,
                    "amount_semantics": "margin",
                    "client_order_id": "qd-quick-7",
                    "is_close": True,
                    "close_side": "long",
                    "close_scope": "full",
                    "entry_price": 74000,
                }
            },
            "created_at": datetime(2026, 10, 18, 3, 35),
        }]

    def close(self):
        return None


class _Db:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _InsertCursor(_Cursor):
    def fetchone(self):
        return {"id": 17}


class _WritableDb(_Db):
    def __init__(self, cursor):
        super().__init__(cursor)
        self.committed = False

    def commit(self):
        self.committed = True


def test_record_quick_trade_persists_client_order_id(monkeypatch):
    cursor = _InsertCursor()
    database = _WritableDb(cursor)

    @contextmanager
    def fake_connection():
        yield database

    monkeypatch.setattr(quick_trade, "get_db_connection", fake_connection)

    trade_id = quick_trade._record_quick_trade(
        user_id=99,
        credential_id=305,
        exchange_id="okx",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        amount=0.01,
        price=0,
        leverage=1,
        market_type="swap",
        tp_price=0,
        sl_price=0,
        status="submitted",
        exchange_order_id="",
        filled=0,
        avg_price=0,
        error_msg="",
        source="quick_trade",
        raw_result={},
        client_order_id="client-17",
    )

    assert trade_id == 17
    assert database.committed is True
    assert "client_order_id" in cursor.query
    assert cursor.params[-4] == "client-17"


def test_history_filters_by_account_symbol_and_market(monkeypatch):
    cursor = _Cursor()

    @contextmanager
    def fake_connection():
        yield _Db(cursor)

    monkeypatch.setattr(quick_trade, "get_db_connection", fake_connection)
    monkeypatch.setattr(quick_trade, "_sync_exchange_close_fills", lambda **kwargs: 0)
    app = Flask(__name__)
    handler = inspect.unwrap(quick_trade.get_history)

    with app.test_request_context(
        "/api/quick-trade/history?limit=20&credential_id=305&symbol=BTC/USDT&market_type=perpetual"
    ):
        g.user_id = 99
        response = handler()

    payload = response.get_json()
    assert payload["data"]["source"] == "local_fallback"
    assert "credential_id = %s" in cursor.query
    assert "UPPER(symbol) = UPPER(%s)" in cursor.query
    assert "market_type = %s" in cursor.query
    assert cursor.params == (99, 305, "BTC/USDT", "swap", 20, 0)
    assert payload["data"]["trades"][0]["credential_id"] == 305
    assert payload["data"]["trades"][0]["commission_quote"] == 0.03
    assert payload["data"]["trades"][0]["margin_mode"] == "cross"
    assert payload["data"]["trades"][0]["requested_base_qty"] == 0.0012
    assert payload["data"]["trades"][0]["client_order_id"] == "qd-quick-7"
    assert payload["data"]["trades"][0]["is_close"] is True
    assert payload["data"]["trades"][0]["close_side"] == "long"
    assert payload["data"]["trades"][0]["entry_price"] == 74000
    assert payload["data"]["trades"][0]["realized_pnl"] == 12.5
    assert payload["data"]["trades"][0]["created_at"] == "2026-10-18T03:35:00Z"


def test_exchange_history_prefers_native_fills_over_local_filled_rows(monkeypatch):
    cursor = _Cursor()

    @contextmanager
    def fake_connection():
        yield _Db(cursor)

    exchange_fill = {
        "id": "exchange-901",
        "symbol": "BTC/USDT",
        "side": "sell",
        "order_type": "fill",
        "amount": 81.0,
        "price": 0.0,
        "leverage": 0,
        "market_type": "swap",
        "status": "filled",
        "exchange_order_id": "901",
        "filled_amount": 0.001,
        "avg_fill_price": 81000.0,
        "commission": 0.0324,
        "commission_ccy": "USDT",
        "commission_quote": 0.0324,
        "realized_pnl": 0.0,
        "source": "exchange",
        "is_close": False,
        "close_side": "",
        "created_at": "2026-10-18T03:36:00+00:00",
    }
    monkeypatch.setattr(quick_trade, "get_db_connection", fake_connection)
    monkeypatch.setattr(quick_trade, "_sync_exchange_close_fills", lambda **kwargs: 0)
    from app.services.quick_trade import trade_history
    monkeypatch.setattr(trade_history, "build_exchange_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(trade_history, "create_exchange_client", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        trade_history,
        "exchange_trade_history",
        lambda *args, **kwargs: {"supported": True, "trades": [exchange_fill]},
    )
    app = Flask(__name__)
    handler = inspect.unwrap(quick_trade.get_history)

    with app.test_request_context(
        "/api/quick-trade/history?limit=20&credential_id=305&symbol=BTC/USDT&market_type=swap"
    ):
        g.user_id = 99
        response = handler()

    payload = response.get_json()
    assert payload["data"]["source"] == "exchange"
    assert payload["data"]["trades"] == [exchange_fill]
