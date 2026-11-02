from contextlib import contextmanager

import pytest
from flask import Flask, g

from app.services.live_trading import execution_lock
from app.services.quick_trade import order_safety


class FakeCursor:
    def __init__(self, acquisitions):
        self.acquisitions = iter(acquisitions)
        self.queries = []
        self.closed = False

    def execute(self, query, params):
        self.queries.append((query, params))

    def fetchone(self):
        return {"acquired": next(self.acquisitions)}

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_trade_execution_key_normalizes_market_and_symbol():
    assert execution_lock.trade_execution_key(
        user_id=7,
        credential_id=9,
        market_type="perpetual",
        symbol="solusdt",
    ) == "trade-execution:7:9:swap:SOL/USDT"


@pytest.mark.parametrize(
    ("body", "expected_market"),
    [
        ({"market_type": "perpetual", "leverage": 1}, "swap"),
        ({"leverage": 5}, "swap"),
        ({"leverage": 1}, "spot"),
    ],
)
def test_quick_trade_uses_same_strategy_lock_key(body, expected_market):
    payload = {
        "credential_id": 9,
        "symbol": "solusdt",
        **body,
    }

    assert order_safety.quick_trade_execution_key(payload, user_id=7) == execution_lock.trade_execution_key(
        user_id=7,
        credential_id=9,
        market_type=expected_market,
        symbol="solusdt",
    )


def test_quick_trade_order_is_serialized_with_strategy_lock_key(monkeypatch):
    acquired = []

    @contextmanager
    def fake_lock(key, *, timeout_sec):
        acquired.append((key, timeout_sec))
        yield

    monkeypatch.setattr(order_safety, "trade_execution_lock", fake_lock)

    @order_safety.serialized_quick_trade_order
    def submit(body):
        return body["symbol"]

    app = Flask(__name__)
    with app.test_request_context():
        g.user_id = 7
        result = submit(
            {
                "credential_id": 9,
                "symbol": "solusdt",
                "market_type": "perpetual",
            }
        )

    assert result == "solusdt"
    assert acquired == [("trade-execution:7:9:swap:SOL/USDT", 3.0)]


def test_trade_execution_lock_releases_acquired_session_lock(monkeypatch):
    cursor = FakeCursor([True])
    monkeypatch.setattr(
        execution_lock,
        "get_db_connection",
        lambda: FakeConnection(cursor),
    )

    with execution_lock.trade_execution_lock("account-symbol", timeout_sec=0):
        pass

    assert "pg_try_advisory_lock" in cursor.queries[0][0]
    assert "pg_advisory_unlock" in cursor.queries[-1][0]
    assert cursor.closed is True


def test_trade_execution_lock_reports_contention_without_unlock(monkeypatch):
    cursor = FakeCursor([False])
    monkeypatch.setattr(
        execution_lock,
        "get_db_connection",
        lambda: FakeConnection(cursor),
    )

    with pytest.raises(execution_lock.TradeExecutionBusy):
        with execution_lock.trade_execution_lock("account-symbol", timeout_sec=0):
            pass

    assert len(cursor.queries) == 1
    assert "pg_try_advisory_lock" in cursor.queries[0][0]
    assert cursor.closed is True
