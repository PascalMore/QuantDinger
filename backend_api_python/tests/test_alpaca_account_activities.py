from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.alpaca_trading.client import AlpacaClient, AlpacaConfig
from app.services.live_trading.alpaca_activity_reconciliation import (
    _activity_allocations,
    _allocated_amount,
    _business_date,
    _fill_allocations,
)


def test_account_activities_paginates_with_last_activity_id(monkeypatch):
    first = [{"id": "fee-1", "activity_type": "FEE"}, {"id": "fee-2", "activity_type": "FEE"}]
    second = [{"id": "fee-3", "activity_type": "INT"}]
    responses = [first, second]
    calls = []

    def fake_get(url, *, params, headers, timeout):
        calls.append((url, dict(params), dict(headers), timeout))
        response = MagicMock()
        response.json.return_value = responses.pop(0)
        response.raise_for_status.return_value = None
        return response

    monkeypatch.setattr("app.services.alpaca_trading.client.requests.get", fake_get)
    client = AlpacaClient(AlpacaConfig(api_key="PK-test", secret_key="secret", paper=True, timeout=7))
    client._trading_client = SimpleNamespace()
    client._account_id = "account-1"

    rows = client.get_account_activities(
        activity_types=["INT", "FEE", "FEE"], after="2026-07-01T00:00:00Z", page_size=2,
    )

    assert [row["id"] for row in rows] == ["fee-1", "fee-2", "fee-3"]
    assert calls[0][0] == "https://paper-api.alpaca.markets/v2/account/activities"
    assert calls[0][1]["activity_types"] == "FEE,INT"
    assert calls[0][1]["after"] == "2026-07-01T00:00:00Z"
    assert "page_token" not in calls[0][1]
    assert calls[1][1]["page_token"] == "fee-2"
    assert calls[0][2]["APCA-API-KEY-ID"] == "PK-test"
    assert calls[0][3] == 7


def test_fill_history_uses_exchange_fill_activities_and_execution_time(monkeypatch):
    client = AlpacaClient(AlpacaConfig(api_key="PK-test", secret_key="secret", paper=True))
    activities = [
        {
            "activity_type": "FILL",
            "id": "fill-1",
            "order_id": "order-1",
            "symbol": "AAPL",
            "side": "sell",
            "qty": "1.25",
            "price": "205.50",
            "transaction_time": "2026-10-23T08:30:01.123Z",
            "type": "partial_fill",
        },
        {
            "activity_type": "FILL",
            "id": "fill-2",
            "order_id": "order-2",
            "symbol": "MSFT",
            "side": "buy",
            "qty": "2",
            "price": "410",
            "transaction_time": "2026-10-23T08:29:00Z",
            "type": "fill",
        },
    ]
    captured = {}

    def fake_activities(**kwargs):
        captured.update(kwargs)
        return activities

    monkeypatch.setattr(client, "get_account_activities", fake_activities)

    rows = client.get_fill_history(symbol="AAPL", limit=20)

    assert captured == {
        "activity_types": ["FILL"],
        "direction": "desc",
        "page_size": 100,
        "max_pages": 20,
    }
    assert rows == [{
        "id": "exchange-alpaca-fill-1",
        "exchange_fill_id": "fill-1",
        "exchange_order_id": "order-1",
        "client_order_id": "",
        "symbol": "AAPL",
        "side": "sell",
        "order_type": "fill",
        "status": "filled",
        "market_type": "spot",
        "amount": pytest.approx(256.875),
        "filled_amount": pytest.approx(1.25),
        "avg_fill_price": pytest.approx(205.5),
        "commission": 0.0,
        "commission_ccy": "USD",
        "commission_quote": None,
        "fees_by_ccy": {},
        "fee_status": "pending",
        "realized_pnl": None,
        "source": "exchange",
        "filled_at": "2026-10-23T08:30:01.123Z",
        "created_at": "2026-10-23T08:30:01.123Z",
        "activity_type": "FILL",
        "fill_type": "partial_fill",
    }]


def test_bulk_taf_allocation_excludes_manual_trade_from_strategy_share():
    activity = {
        "id": "taf-1", "activity_type": "FEE", "activity_subtype": "TAF",
        "date": "2026-07-23", "net_amount": "-0.03",
    }
    fills = [
        {"id": "fill-strategy", "order_id": "strategy-order", "date": "2026-07-22", "side": "sell",
         "qty": "2", "price": "100"},
        {"id": "fill-manual", "order_id": "manual-order", "date": "2026-07-22", "side": "sell",
         "qty": "8", "price": "100"},
    ]
    owners = {"strategy-order": {"strategy_id": 11, "user_id": 7}}

    allocations = _fill_allocations(activity, fills, owners)

    assert allocations == [(11, 7, pytest.approx(0.2), "account_fill_share", 0.0)]


def test_bulk_reg_allocation_uses_sell_notional_and_system_date():
    activity = {
        "id": "reg-1", "activity_type": "FEE", "activity_subtype": "REG",
        "date": "2026-07-23", "details": {"system_date": "2026-07-22"},
    }
    fills = [
        {"order_id": "strategy-order", "date": "2026-07-22", "side": "sell", "qty": "1", "price": "200"},
        {"order_id": "manual-order", "date": "2026-07-22", "side": "sell", "qty": "4", "price": "50"},
        {"order_id": "strategy-buy", "date": "2026-07-22", "side": "buy", "qty": "10", "price": "200"},
    ]
    owners = {
        "strategy-order": {"strategy_id": 11, "user_id": 7},
        "strategy-buy": {"strategy_id": 11, "user_id": 7},
    }

    allocations = _fill_allocations(activity, fills, owners)

    assert _business_date(activity, bulk_fee=True) == date(2026, 7, 22)
    assert allocations == [(11, 7, pytest.approx(0.5), "account_fill_share", 0.0)]


def test_parent_order_fee_is_assigned_exactly_and_existing_commission_is_not_double_counted():
    activity = {
        "id": "commission-activity", "activity_type": "FEE", "activity_subtype": "COM",
        "details": {"order_id": "strategy-order"}, "net_amount": "-1.50",
    }
    owners = {
        "strategy-order": {
            "strategy_id": 11, "user_id": 7, "recorded_commission": 0.40,
        }
    }

    allocations = _activity_allocations(
        activity, fills=[], owners=owners, account_positions=[], credential_id=3,
    )

    assert allocations == [(11, 7, 1.0, "parent_order", 0.40)]
    assert _allocated_amount(-1.50, 1.0, "COM", 0.40) == pytest.approx(-1.10)
    assert _allocated_amount(-0.30, 1.0, "COM", 0.40) == 0.0


def test_allocation_ratio_is_clamped_to_account_activity_amount():
    assert _allocated_amount(-2.0, 1.5, "ADR") == -2.0
    assert _allocated_amount(-2.0, -0.5, "ADR") == 0.0
