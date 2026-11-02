import pytest

from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient
from app.services.quick_trade import trade_history


def test_gate_trade_history_uses_exchange_fills(monkeypatch):
    client = object.__new__(GateUsdtFuturesClient)
    monkeypatch.setattr(
        client,
        "_signed_request",
        lambda *args, **kwargs: [{
            "id": "gate-fill-1",
            "order_id": "gate-order-1",
            "contract": "BTC_USDT",
            "size": "-10",
            "close_size": "10",
            "price": "81000",
            "fee": "-0.2",
            "pnl": "1.5",
            "create_time": "1792692260",
        }],
    )
    monkeypatch.setattr(
        client,
        "contracts_signed_to_base_qty",
        lambda **kwargs: abs(kwargs["contracts_signed"]) * 0.01,
    )

    result = trade_history.gate_trade_history(client, "BTC/USDT", "swap", 50)

    fill = result["trades"][0]
    assert fill["source"] == "exchange"
    assert fill["side"] == "sell"
    assert fill["filled_amount"] == pytest.approx(0.1)
    assert fill["is_close"] is True
    assert fill["close_side"] == "long"
    assert fill["realized_pnl"] == pytest.approx(1.5)


def test_okx_trade_history_uses_exchange_fills(monkeypatch):
    client = object.__new__(OkxClient)
    monkeypatch.setattr(trade_history, "contract_multiplier", lambda *args: 0.01)
    monkeypatch.setattr(
        client,
        "_signed_request",
        lambda *args, **kwargs: {
            "data": [{
                "tradeId": "okx-fill-1",
                "ordId": "okx-order-1",
                "instId": "BTC-USDT-SWAP",
                "side": "sell",
                "posSide": "long",
                "fillSz": "10",
                "fillPx": "81000",
                "fee": "-0.2",
                "feeCcy": "USDT",
                "fillPnl": "1.5",
                "fillTime": "1792692260000",
            }]
        },
    )

    result = trade_history.okx_trade_history(client, "BTC/USDT", "swap", 50)

    fill = result["trades"][0]
    assert fill["filled_amount"] == pytest.approx(0.1)
    assert fill["commission_quote"] == pytest.approx(0.2)
    assert fill["is_close"] is True
    assert fill["close_side"] == "long"
    assert fill["realized_pnl"] == pytest.approx(1.5)


def test_bitget_trade_history_uses_uta_v3_fills(monkeypatch):
    client = object.__new__(BitgetMixClient)
    monkeypatch.setattr(
        client,
        "_signed_request",
        lambda *args, **kwargs: {
            "data": {
                "list": [{
                    "execId": "bitget-fill-1",
                    "orderId": "bitget-order-1",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "tradeSide": "close",
                    "posSide": "short",
                    "execQty": "0.1",
                    "execPrice": "81000",
                    "execPnl": "1.5",
                    "feeDetail": [{"feeCoin": "USDT", "fee": "0.2"}],
                    "createdTime": "1792692260000",
                }]
            }
        },
    )

    result = trade_history.bitget_trade_history(client, "BTC/USDT", "swap", 50)

    fill = result["trades"][0]
    assert fill["filled_amount"] == pytest.approx(0.1)
    assert fill["commission_quote"] == pytest.approx(0.2)
    assert fill["is_close"] is True
    assert fill["close_side"] == "short"
    assert fill["realized_pnl"] == pytest.approx(1.5)


def test_bitget_trade_history_pages_until_requested_symbol_is_found(monkeypatch):
    client = object.__new__(BitgetMixClient)
    calls = []

    def fake_request(*args, **kwargs):
        calls.append(kwargs["params"])
        if len(calls) == 1:
            return {
                "data": {
                    "list": [{
                        "execId": "eth-fill-1",
                        "symbol": "ETHUSDT",
                        "side": "buy",
                        "execQty": "1",
                        "execPrice": "3000",
                        "createdTime": "1792692260001",
                    }],
                    "cursor": "next-page",
                }
            }
        return {
            "data": {
                "list": [{
                    "execId": "btc-fill-1",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "execQty": "0.1",
                    "execPrice": "81000",
                    "createdTime": "1792692260000",
                }],
                "cursor": "",
            }
        }

    monkeypatch.setattr(client, "_signed_request", fake_request)

    result = trade_history.bitget_trade_history(client, "BTC/USDT", "swap", 1)

    assert len(calls) == 2
    assert calls[1]["cursor"] == "next-page"
    assert [fill["id"] for fill in result["trades"]] == ["exchange-bitget-btc-fill-1"]


def test_bybit_trade_history_uses_v5_executions(monkeypatch):
    client = object.__new__(BybitClient)
    client.category = "linear"
    monkeypatch.setattr(
        client,
        "get_executions",
        lambda **kwargs: {
            "result": {
                "list": [{
                    "execId": "bybit-fill-1",
                    "orderId": "bybit-order-1",
                    "symbol": "BTCUSDT",
                    "side": "Sell",
                    "positionIdx": 1,
                    "closedSize": "0.1",
                    "execQty": "0.1",
                    "execPrice": "81000",
                    "execFee": "0.2",
                    "feeCurrency": "USDT",
                    "execTime": "1792692260000",
                    "execType": "Trade",
                }]
            }
        },
    )

    result = trade_history.bybit_trade_history(client, "BTC/USDT", "swap", 50)

    fill = result["trades"][0]
    assert fill["filled_amount"] == pytest.approx(0.1)
    assert fill["commission_quote"] == pytest.approx(0.2)
    assert fill["is_close"] is True
    assert fill["close_side"] == "long"


def test_htx_trade_history_uses_exchange_match_results(monkeypatch):
    client = object.__new__(HtxClient)
    client.market_type = "swap"
    client.margin_mode = "cross"
    calls = []
    monkeypatch.setattr(trade_history, "contract_multiplier", lambda *args: 0.01)

    def fake_request(method, endpoint, json_body):
        calls.append((method, endpoint, json_body))
        return {
            "code": 200,
            "data": [{
                "id": "htx-fill-1",
                "order_id_str": "htx-order-1",
                "contract_code": "BTC-USDT",
                "direction": "sell",
                "offset": "close",
                "trade_volume": "10",
                "trade_price": "81000",
                "trade_fee": "-0.2",
                "fee_asset": "USDT",
                "real_profit": "1.5",
                "create_date": "1792692260000",
            }],
            "next_id": 0,
            "remain_size": 0,
        }

    monkeypatch.setattr(client, "_swap_private_request_raw", fake_request)

    result = trade_history.htx_trade_history(client, "BTC/USDT", "swap", 50)

    assert calls == [(
        "POST",
        "/linear-swap-api/v3/swap_cross_matchresults_exact",
        {"contract": "BTC-USDT", "trade_type": 0, "direct": "prev"},
    )]
    fill = result["trades"][0]
    assert fill["id"] == "exchange-htx-htx-fill-1"
    assert fill["exchange_order_id"] == "htx-order-1"
    assert fill["filled_amount"] == pytest.approx(0.1)
    assert fill["commission_quote"] == pytest.approx(0.2)
    assert fill["is_close"] is True
    assert fill["close_side"] == "long"
    assert fill["realized_pnl"] == pytest.approx(1.5)
