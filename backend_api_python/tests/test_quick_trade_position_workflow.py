import inspect
from types import SimpleNamespace

import pytest
from flask import Flask, g

from app.routes import quick_trade
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.quick_trade.position_activity import (
    binance_position_history,
    bybit_position_history,
    gate_native_protection_fills,
    gate_position_history,
    gate_protection_orders,
    htx_position_history,
    okx_position_history,
    quick_trade_position_history,
    tracked_quick_trade_legs,
    validate_exit_price,
)


@pytest.mark.parametrize("status,left,expected", [("finished", 0, "filled"), ("open", 2, "open"), ("finished", 1, "cancelled")])
def test_gate_ack_is_authoritative_without_another_order_poll(monkeypatch, status, left, expected):
    from app.services.quick_trade.orders import enrich_fill
    client = object.__new__(GateUsdtFuturesClient)
    monkeypatch.setattr(client, "contracts_signed_to_base_qty", lambda **kw: kw["contracts_signed"] * .1)
    monkeypatch.setattr(client, "get_futures_trades_for_order", lambda **kw: (.02, "USDT"))
    monkeypatch.setattr(client, "wait_for_fill", lambda **kw: pytest.fail("must not poll an authoritative ACK"))
    ack = dict(id="123", status=status, size=2, left=left, fill_price=100)
    result = enrich_fill(client, order_id="123", symbol="SOL/USDT", market_type="swap", initial_order=ack)
    assert result["status"] == expected
    assert result["filled"] == (2 - left) * .1
    assert result["fee"] == (.02 if left < 2 else 0)


def test_gate_ack_can_defer_fee_lookup_from_interactive_request(monkeypatch):
    from app.services.quick_trade.orders import enrich_fill

    client = object.__new__(GateUsdtFuturesClient)
    monkeypatch.setattr(client, "contracts_signed_to_base_qty", lambda **kw: kw["contracts_signed"] * .1)
    monkeypatch.setattr(client, "get_futures_trades_for_order", lambda **kw: pytest.fail("fee lookup must be deferred"))
    ack = dict(id="123", status="finished", size=2, left=0, fill_price=100)

    result = enrich_fill(
        client,
        order_id="123",
        symbol="SOL/USDT",
        market_type="swap",
        initial_order=ack,
        include_fees=False,
    )

    assert result["filled"] == .2
    assert result["avg_price"] == 100
    assert result["status"] == "filled"
    assert result["fee_status"] == "pending"


def test_quick_trade_transport_keeps_shared_pool_warm_on_error(monkeypatch):
    from app.services.quick_trade import transport

    session = SimpleNamespace()
    releases = []
    monkeypatch.setattr(transport, "get_exchange_http_session", lambda: session)
    monkeypatch.setattr(transport, "release_exchange_http_session", lambda: releases.append(True))
    app = Flask(__name__)
    for _ in range(2):
        with app.test_request_context():
            transport.open_quick_trade_transport()
            assert g.quick_trade_session is session
            transport.close_quick_trade_transport(RuntimeError("failed"))
            assert not hasattr(g, "quick_trade_session")
    assert releases == [True, True]


def test_order_reconciliation_uses_generic_exchange_fill_snapshot():
    from app.services.quick_trade.order_reconciliation import _exchange_order_updates

    class Client:
        def wait_for_fill(self, **kwargs):
            assert kwargs["max_wait_sec"] == 0
            return {"filled": 0.01, "avg_price": 81047.8, "status": "filled"}

    rows = [{
        "id": 7,
        "exchange_order_id": "order-7",
        "filled_amount": 0,
        "avg_fill_price": 0,
        "raw_result": {"_quick_trade": {"requested_base_qty": 0.01}},
    }]

    updates = _exchange_order_updates(Client(), "BTC/USDT", "swap", rows)

    assert updates[7]["status"] == "filled"
    assert updates[7]["filled_amount"] == pytest.approx(0.01)
    assert updates[7]["avg_fill_price"] == pytest.approx(81047.8)


def test_exchange_history_preserves_round_trip_net_and_funding(monkeypatch):
    client = object.__new__(GateUsdtFuturesClient)
    rows = [dict(contract="BTC_USDT", side="long", first_open_time=1000, time=2000,
                 long_price="100", short_price="110", accum_size="10", pnl="0.8",
                 pnl_pnl="1", pnl_fee="-0.3", pnl_fund="0.1")]
    monkeypatch.setattr(client, "_signed_request", lambda *a, **k: rows)
    monkeypatch.setattr(client, "contracts_signed_to_base_qty", lambda **k: k["contracts_signed"] * .01)
    result = gate_position_history(client, "BTC/USDT")["positions"][0]
    assert result["quantity"] == .1
    assert result["pnl"] == .8
    assert result["fee"] == .3
    assert result["funding"] == .1
    assert result["entry_price"] == 100
    assert result["opened_at"].endswith("+00:00")
    rows[0]["side"] = "short"
    result = gate_position_history(client, "BTC/USDT")["positions"][0]
    assert result["entry_price"] == 110
    assert result["exit_price"] == 100


def test_okx_position_history_uses_exchange_closed_cycle(monkeypatch):
    from app.services.live_trading.okx import OkxClient

    client = object.__new__(OkxClient)
    monkeypatch.setattr(
        client,
        "_signed_request",
        lambda *args, **kwargs: {
            "code": "0",
            "data": [{
                "posId": "position-1",
                "instId": "BTC-USDT-SWAP",
                "direction": "long",
                "openAvgPx": "81047.8",
                "closeAvgPx": "81030",
                "closeTotalPos": "0.01",
                "pnl": "-0.00178",
                "realizedPnl": "-0.00988389",
                "fee": "-0.00810389",
                "fundingFee": "0",
                "cTime": "1792692255278",
                "uTime": "1792692268315",
            }],
        },
    )

    result = okx_position_history(client, "BTC/USDT")

    assert result["supported"] is True
    assert len(result["positions"]) == 1
    position = result["positions"][0]
    assert position["entry_price"] == pytest.approx(81047.8)
    assert position["exit_price"] == pytest.approx(81030)
    assert position["quantity"] == pytest.approx(0.01)
    assert position["gross_pnl"] == pytest.approx(-0.00178)
    assert position["fee"] == pytest.approx(0.00810389)
    assert position["pnl"] == pytest.approx(-0.00988389)


def test_binance_position_history_rebuilds_exchange_fill_cycle(monkeypatch):
    from app.services.live_trading.binance import BinanceFuturesClient

    client = object.__new__(BinanceFuturesClient)
    rows = [
        {
            "id": 1,
            "symbol": "BTCUSDT",
            "side": "BUY",
            "positionSide": "LONG",
            "price": "81000",
            "qty": "0.01",
            "realizedPnl": "0",
            "commission": "0.0081",
            "commissionAsset": "USDT",
            "time": 1792692200000,
        },
        {
            "id": 2,
            "symbol": "BTCUSDT",
            "side": "SELL",
            "positionSide": "LONG",
            "price": "81100",
            "qty": "0.01",
            "realizedPnl": "1",
            "commission": "0.00811",
            "commissionAsset": "USDT",
            "time": 1792692260000,
        },
    ]
    monkeypatch.setattr(client, "get_user_trades", lambda **kwargs: rows)

    result = binance_position_history(client, "BTC/USDT")

    assert result["supported"] is True
    assert len(result["positions"]) == 1
    position = result["positions"][0]
    assert position["side"] == "long"
    assert position["entry_price"] == pytest.approx(81000)
    assert position["exit_price"] == pytest.approx(81100)
    assert position["quantity"] == pytest.approx(0.01)
    assert position["gross_pnl"] == pytest.approx(1)
    assert position["fee"] == pytest.approx(0.01621)
    assert position["pnl"] == pytest.approx(0.98379)


def test_binance_trade_history_uses_exchange_fills_and_infers_short_close(monkeypatch):
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.quick_trade.position_activity import binance_trade_history

    client = object.__new__(BinanceFuturesClient)
    rows = [
        {
            "id": 10,
            "orderId": 100,
            "symbol": "SOLUSDT",
            "side": "SELL",
            "positionSide": "BOTH",
            "price": "104.03",
            "qty": "4.8",
            "realizedPnl": "0",
            "commission": "0.10",
            "commissionAsset": "USDT",
            "time": 1792692200000,
        },
        {
            "id": 11,
            "orderId": 101,
            "symbol": "SOLUSDT",
            "side": "BUY",
            "positionSide": "BOTH",
            "price": "103.92",
            "qty": "4.8",
            "realizedPnl": "0.528",
            "commission": "0.10",
            "commissionAsset": "USDT",
            "time": 1792692260000,
        },
    ]
    monkeypatch.setattr(client, "get_user_trades", lambda **kwargs: rows)

    result = binance_trade_history(client, "SOL/USDT", market_type="swap")

    assert result["supported"] is True
    assert len(result["trades"]) == 2
    close_fill = result["trades"][0]
    assert close_fill["source"] == "exchange"
    assert close_fill["order_type"] == "fill"
    assert close_fill["is_close"] is True
    assert close_fill["close_side"] == "short"
    assert close_fill["realized_pnl"] == pytest.approx(0.528)
    assert close_fill["filled_amount"] == pytest.approx(4.8)
    assert close_fill["avg_fill_price"] == pytest.approx(103.92)
    assert close_fill["commission_quote"] == pytest.approx(0.10)
    assert result["trades"][1]["realized_pnl"] is None


def test_gate_native_protection_fills_groups_partial_fills_and_ignores_regular_orders(monkeypatch):
    client = object.__new__(GateUsdtFuturesClient)
    rows = [
        dict(id="f1", order_id="o1", text="ao-trigger", contract="BTC_USDT", size="-1000", price="81700", fee="4", create_time=1000),
        dict(id="f2", order_id="o1", text="ao-trigger", contract="BTC_USDT", size="-530", price="81720", fee="2.2", create_time=1001),
        dict(id="f3", order_id="o2", text="t-quick", contract="BTC_USDT", size="100", price="81600", fee=".4", create_time=1002),
        dict(id="f4", order_id="o3", text="ao-other", contract="ETH_USDT", size="-10", price="3000", fee=".1", create_time=1003),
    ]
    monkeypatch.setattr(client, "_signed_request", lambda *a, **k: rows)
    monkeypatch.setattr(client, "contracts_signed_to_base_qty", lambda **k: abs(k["contracts_signed"]) * .0001)

    result = gate_native_protection_fills(client, "BTC/USDT")

    assert result["supported"] is True
    assert len(result["fills"]) == 1
    fill = result["fills"][0]
    assert fill["exchange_order_id"] == "o1"
    assert fill["side"] == "sell"
    assert fill["close_side"] == "long"
    assert fill["filled_amount"] == pytest.approx(.153)
    assert fill["avg_fill_price"] == pytest.approx((1000 * 81700 + 530 * 81720) / 1530)
    assert fill["commission"] == pytest.approx(6.2)
    assert fill["exchange_fill_ids"] == ["f1", "f2"]
    assert fill["created_at"].endswith("+00:00")


def test_tracked_legs_exclude_recorded_closes_and_collect_fill_references():
    rows = [
        {
            "id": 1,
            "side": "buy",
            "status": "filled",
            "filled_amount": 0.15,
            "avg_fill_price": 100,
            "leverage": 5,
            "exchange_order_id": "entry-1",
            "created_at": "2026-10-18T08:00:00+00:00",
            "raw_result": {},
        },
        {
            "id": 2,
            "side": "sell",
            "status": "filled",
            "filled_amount": 0.05,
            "avg_fill_price": 110,
            "exchange_order_id": "close-1",
            "created_at": "2026-10-18T08:10:00+00:00",
            "raw_result": {
                "exchange_close": {"exchange_fill_ids": ["fill-1"]},
                "_quick_trade": {"is_close": True, "close_side": "long"},
            },
        },
    ]
    tracked = tracked_quick_trade_legs(rows, "swap")
    assert tracked["legs"] == [
        {
            "side": "long",
            "quantity": pytest.approx(0.10),
            "entry_price": pytest.approx(100),
            "opened_at": "2026-10-18T08:00:00+00:00",
            "leverage": 5,
        }
    ]
    assert tracked["recorded_ids"] == {"entry-1", "close-1", "fill-1"}


def test_reconciled_position_history_pairs_entry_and_exchange_close():
    rows = [
        {
            "id": 1,
            "side": "buy",
            "status": "filled",
            "filled_amount": 2,
            "avg_fill_price": 100,
            "commission_quote": 0.2,
            "created_at": "2026-10-18T08:00:00+00:00",
            "raw_result": {},
        },
        {
            "id": 2,
            "side": "sell",
            "status": "filled",
            "filled_amount": 2,
            "avg_fill_price": 110,
            "commission_quote": 0.3,
            "created_at": "2026-10-18T08:10:00+00:00",
            "raw_result": {"_quick_trade": {"is_close": True, "close_side": "long"}},
        },
    ]
    result = quick_trade_position_history(rows, "BTC/USDT")
    assert result["supported"] is True
    assert len(result["positions"]) == 1
    position = result["positions"][0]
    assert position["quantity"] == pytest.approx(2)
    assert position["gross_pnl"] == pytest.approx(20)
    assert position["fee"] == pytest.approx(0.5)
    assert position["pnl"] == pytest.approx(19.5)


def test_bitget_position_history_uses_exchange_closed_cycle(monkeypatch):
    from app.services.live_trading.bitget_classic import BitgetClassicMixClient
    from app.services.quick_trade.position_activity import bitget_position_history

    client = object.__new__(BitgetClassicMixClient)
    monkeypatch.setattr(
        client,
        "_signed_request",
        lambda *args, **kwargs: {
            "data": {
                "list": [{
                    "positionId": "position-1",
                    "symbol": "BTCUSDT",
                    "holdSide": "long",
                    "openAvgPrice": "81270",
                    "closeAvgPrice": "81256.6",
                    "openTotalPos": "0.0001",
                    "closeTotalPos": "0.0001",
                    "pnl": "-0.00134",
                    "netProfit": "-0.00784079",
                    "totalFunding": "0",
                    "openFee": "-0.0016254",
                    "closeFee": "-0.00487539",
                    "ctime": "1792691557318",
                    "utime": "1792691574349",
                }]
            }
        },
    )

    result = bitget_position_history(client, "BTC/USDT")

    assert result["supported"] is True
    assert len(result["positions"]) == 1
    position = result["positions"][0]
    assert position["entry_price"] == pytest.approx(81270)
    assert position["exit_price"] == pytest.approx(81256.6)
    assert position["quantity"] == pytest.approx(0.0001)
    assert position["gross_pnl"] == pytest.approx(-0.00134)
    assert position["fee"] == pytest.approx(0.00650079)
    assert position["pnl"] == pytest.approx(-0.00784079)


@pytest.mark.parametrize("side,kind,valid,invalid", [
    ("long", "tp", 110, 90), ("long", "sl", 90, 110),
    ("short", "tp", 90, 110), ("short", "sl", 110, 90),
])
def test_protection_direction_is_validated_against_current_mark(side, kind, valid, invalid):
    validate_exit_price(side, kind, valid, 100)
    for price in (invalid, 100, float("nan"), float("inf"), 0):
        with pytest.raises(ValueError):
            validate_exit_price(side, kind, price, 100)


def test_protection_list_excludes_other_symbols_and_nonreducing_orders(monkeypatch):
    client = object.__new__(GateUsdtFuturesClient)
    rows = [dict(id=1, initial=dict(contract="BTC_USDT", size=-45, reduce_only=True), trigger=dict(price="110", rule=1)),
            dict(id=2, initial=dict(contract="ETH_USDT", size=-45, reduce_only=True), trigger={}),
            dict(id=3, initial=dict(contract="BTC_USDT", size=45), trigger={})]
    monkeypatch.setattr(client, "_signed_request", lambda *a, **k: rows)
    assert gate_protection_orders(client, "BTC/USDT")["orders"] == [dict(id="1", side="long", price=110, kind="tp")]


def test_gate_response_uses_is_reduce_only_and_string_order_id(monkeypatch):
    client = object.__new__(GateUsdtFuturesClient)
    rows = [dict(id=2111713079019438080, id_string="2111713079019438081",
                 initial=dict(contract="SOL_USDT", size=-1, is_reduce_only=True),
                 trigger=dict(price="120", rule=1)),
            dict(id=2, initial=dict(contract="SOL_USDT", size=1, is_reduce_only=True),
                 trigger=dict(price="120", rule=1)),
            dict(id=3, initial=dict(contract="SOL_USDT", size=-1, is_reduce_only=False), trigger={})]
    monkeypatch.setattr(client, "_signed_request", lambda *a, **k: rows)
    assert gate_protection_orders(client, "SOL/USDT")["orders"] == [
        dict(id="2111713079019438081", side="long", price=120, kind="tp"),
        dict(id="2", side="short", price=120, kind="sl")]


@pytest.mark.parametrize("order_type", ["market", "limit"])
def test_close_uses_fresh_full_size_reduce_only_and_bounded_enrichment(monkeypatch, order_type):
    from app.services.live_trading import execution
    from app.services.pending_orders import live_order_phases
    from app.services.live_trading import fee_quote
    calls = {}
    monkeypatch.setattr(quick_trade, "_build_product_aware_config", lambda *a, **k: ({"exchange_id": "gate"}, {}))
    monkeypatch.setattr(quick_trade, "create_exchange_client", lambda *a, **k: object())
    monkeypatch.setattr(quick_trade, "_fetch_exchange_positions_raw", lambda *a, **k: [{"symbol": "BTC/USDT", "size": .0061, "side": "buy", "entry_price": 100}])
    def submit(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(exchange_order_id="new-order", filled=0, avg_price=0, raw={})
    monkeypatch.setattr(execution, "place_order_from_signal", submit)
    monkeypatch.setattr(live_order_phases, "place_live_limit_order", submit)
    def enrich(*args, **kwargs):
        assert kwargs["max_wait_sec"] <= .25
        return {"filled": 0, "status": "open"}
    monkeypatch.setattr(quick_trade, "enrich_fill", enrich)
    monkeypatch.setattr(fee_quote, "fee_to_quote", lambda *a, **k: None)
    stored = {}
    monkeypatch.setattr(quick_trade, "_record_quick_trade", lambda **kw: stored.update(kw) or 1)
    app = Flask(__name__)
    with app.test_request_context():
        g.user_id = 101
        response = inspect.unwrap(quick_trade.close_position)({"credential_id": 9, "symbol": "BTC/USDT", "order_type": order_type, "price": 110})
        assert response.json["code"] == 1
    assert calls["amount"] == .0061
    assert stored["order_type"] == order_type
    assert stored["status"] == "submitted"
    if order_type == "limit":
        assert calls["reduce_only"] is True
        assert calls["side"] == "sell"
        assert calls["price"] == 110
    else:
        assert calls["signal_type"] == "close_long"


def test_binance_full_market_close_reports_snapshot_residual_for_one_safe_retry(monkeypatch):
    from app.services.live_trading import execution
    from app.services.live_trading import fee_quote

    snapshots = iter([
        [{"symbol": "SOL/USDT", "size": -4.76, "side": "short", "entry_price": 104.89}],
        [{"symbol": "SOL/USDT", "size": -1.9, "side": "short", "entry_price": 104.89}],
    ])
    calls = []
    monkeypatch.setattr(quick_trade, "_build_product_aware_config", lambda *a, **k: ({"exchange_id": "binance"}, {}))
    monkeypatch.setattr(quick_trade, "create_exchange_client", lambda *a, **k: object())
    monkeypatch.setattr(quick_trade, "_fetch_exchange_positions_raw", lambda *a, **k: next(snapshots))
    monkeypatch.setattr(quick_trade, "exchange_quantity_snapshot", lambda *a, **k: (k["requested"], {}))

    def submit(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            exchange_order_id="close-1",
            filled=2.86,
            avg_price=105.3,
            raw={"status": "FILLED", "executedQty": "2.86"},
        )

    monkeypatch.setattr(execution, "place_order_from_signal", submit)
    monkeypatch.setattr(
        quick_trade,
        "enrich_fill",
        lambda *a, **k: {
            "filled": 2.86,
            "avg_price": 105.3,
            "fee": 0,
            "fee_ccy": "USDT",
            "fees_by_ccy": {"USDT": 0},
            "fee_status": "actual_zero",
            "status": "filled",
        },
    )
    monkeypatch.setattr(fee_quote, "fee_to_quote", lambda *a, **k: 0)
    stored = {}
    monkeypatch.setattr(quick_trade, "_record_quick_trade", lambda **kw: stored.update(kw) or 1)

    app = Flask(__name__)
    with app.test_request_context():
        g.user_id = 101
        response = inspect.unwrap(quick_trade.close_position)({
            "credential_id": 9,
            "symbol": "SOL/USDT",
            "market_type": "swap",
            "position_side": "short",
            "close_scope": "full",
        })

    data = response.json["data"]
    assert calls[0]["amount"] == pytest.approx(4.76)
    assert data["filled"] == pytest.approx(2.86)
    assert data["remaining_size"] == pytest.approx(1.9)
    assert data["retry_close_size"] == pytest.approx(1.9)
    assert data["close_complete"] is False
    assert data["status"] == "partially_filled"
    assert stored["status"] == "partially_filled"


def test_matching_position_size_ignores_other_symbols_and_sides():
    positions = [
        {"symbol": "SOL/USDT", "side": "short", "size": 1.2},
        {"symbol": "SOLUSDT", "side": "short", "size": 0.7},
        {"symbol": "SOL/USDT", "side": "long", "size": 4},
        {"symbol": "BTC/USDT", "side": "short", "size": 8},
    ]
    assert quick_trade._matching_position_size(
        positions,
        symbol="SOL/USDT",
        position_side="short",
    ) == pytest.approx(1.9)


def test_bybit_position_history_uses_exchange_closed_pnl(monkeypatch):
    from app.services.live_trading.bybit import BybitClient

    client = object.__new__(BybitClient)
    client.category = "linear"
    monkeypatch.setattr(
        client,
        "_signed_request",
        lambda *args, **kwargs: {
            "result": {
                "list": [{
                    "symbol": "BTCUSDT",
                    "orderId": "close-1",
                    "side": "Sell",
                    "closedSize": "0.01",
                    "avgEntryPrice": "81000",
                    "avgExitPrice": "81100",
                    "closedPnl": "0.6",
                    "openFee": "0.2",
                    "closeFee": "0.2",
                    "createdTime": "1792691000000",
                    "updatedTime": "1792691001000",
                }]
            }
        },
    )

    position = bybit_position_history(client, "BTC/USDT")["positions"][0]

    assert position["side"] == "long"
    assert position["quantity"] == pytest.approx(0.01)
    assert position["entry_price"] == pytest.approx(81000)
    assert position["exit_price"] == pytest.approx(81100)
    assert position["fee"] == pytest.approx(0.4)
    assert position["pnl"] == pytest.approx(0.6)
    assert position["gross_pnl"] == pytest.approx(1.0)


def test_htx_position_history_rebuilds_exchange_match_cycle(monkeypatch):
    from app.services.live_trading.htx import HtxClient
    from app.services.quick_trade import position_activity

    client = object.__new__(HtxClient)
    client.market_type = "swap"
    client.margin_mode = "cross"
    monkeypatch.setattr(position_activity, "contract_multiplier", lambda *args: 0.01)
    monkeypatch.setattr(
        client,
        "_swap_private_request_raw",
        lambda *args, **kwargs: {
            "data": {
                "trades": [
                    {
                        "contract_code": "BTC-USDT",
                        "trade_id": "open-1",
                        "direction": "buy",
                        "offset": "open",
                        "trade_volume": "10",
                        "trade_price": "81000",
                        "trade_fee": "-0.2",
                        "created_at": "1792691000000",
                    },
                    {
                        "contract_code": "BTC-USDT",
                        "trade_id": "close-1",
                        "direction": "sell",
                        "offset": "close",
                        "trade_volume": "10",
                        "trade_price": "81100",
                        "trade_fee": "-0.2",
                        "real_profit": "10",
                        "created_at": "1792691001000",
                    },
                ]
            }
        },
    )

    position = htx_position_history(client, "BTC/USDT")["positions"][0]

    assert position["side"] == "long"
    assert position["quantity"] == pytest.approx(0.1)
    assert position["entry_price"] == pytest.approx(81000)
    assert position["exit_price"] == pytest.approx(81100)
    assert position["fee"] == pytest.approx(0.4)
    assert position["gross_pnl"] == pytest.approx(10)
    assert position["pnl"] == pytest.approx(9.6)


def test_order_reconciliation_covers_every_crypto_quick_trade_exchange():
    from app.services.quick_trade.order_reconciliation import _RECONCILABLE_EXCHANGES

    assert {"binance", "bitget", "bybit", "gate", "htx", "okx"} <= _RECONCILABLE_EXCHANGES


def test_cancel_protection_cannot_cancel_another_symbol_or_side(monkeypatch):
    from app.services.quick_trade import position_activity
    client = object.__new__(GateUsdtFuturesClient)
    monkeypatch.setattr(quick_trade, "build_exchange_config", lambda cid, uid, opts: {"owner": uid})
    monkeypatch.setattr(quick_trade, "create_exchange_client", lambda *a, **k: client)
    monkeypatch.setattr(position_activity, "gate_protection_orders", lambda *a: {"orders": [dict(id="1", side="short")]})
    monkeypatch.setattr(client, "_signed_request", lambda *a, **k: pytest.fail("must not cancel"))
    app = Flask(__name__)
    with app.test_request_context():
        g.user_id = 101
        _, status = inspect.unwrap(quick_trade.set_position_protection)({"credential_id": 9, "symbol": "BTC/USDT", "position_side": "long", "action": "cancel", "order_id": "1"})
        assert status == 409
