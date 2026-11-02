"""Exchange-owned position history and conditional exits for Quick Trade."""

import json
from datetime import datetime, timezone
from math import isfinite

from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_classic import BitgetClassicMixClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.fill_accounting import contract_multiplier
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient
from app.services.live_trading.symbols import (
    to_bitget_um_symbol,
    to_binance_futures_symbol,
    to_bybit_symbol,
    to_gate_currency_pair,
    to_htx_contract_code,
    to_okx_swap_inst_id,
)
from app.utils.logger import get_logger


logger = get_logger(__name__)


def utc_time(value):
    if not value:
        return None
    timestamp = float(value)
    if abs(timestamp) > 100_000_000_000:
        timestamp /= 1000.0
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def gate_position_history(client, symbol, limit=50):
    if not isinstance(client, GateUsdtFuturesClient):
        return {"supported": False, "positions": []}
    contract = to_gate_currency_pair(symbol)
    rows = client._signed_request("GET", "/api/v4/futures/usdt/position_close", params={"contract": contract, "limit": limit})
    result = []
    for row in rows or []:
        if row.get("contract") != contract:
            continue
        long = row.get("side") == "long"
        result.append({
            "id": str(row.get("time_us") or row.get("time")) + str(row.get("side")),
            "symbol": symbol, "side": row.get("side"),
            "opened_at": utc_time(row.get("first_open_time")), "closed_at": utc_time(row.get("time")),
            "entry_price": float(row.get("long_price" if long else "short_price") or 0),
            "exit_price": float(row.get("short_price" if long else "long_price") or 0),
            "quantity": client.contracts_signed_to_base_qty(contract=contract, contracts_signed=float(row.get("accum_size") or 0)),
            "pnl": float(row["pnl"]) if row.get("pnl") is not None else None,
            "gross_pnl": float(row["pnl_pnl"]) if row.get("pnl_pnl") is not None else None,
            "fee": -float(row["pnl_fee"]) if row.get("pnl_fee") is not None else None,
            "funding": float(row["pnl_fund"]) if row.get("pnl_fund") is not None else None,
            "source": "exchange",
        })
    return {"supported": True, "positions": result}


def okx_position_history(client, symbol, limit=50):
    """Return exchange-owned closed position cycles for OKX swaps."""
    if not isinstance(client, OkxClient):
        return {"supported": False, "positions": []}
    inst_id = to_okx_swap_inst_id(symbol)
    try:
        raw = client._signed_request(
            "GET",
            "/api/v5/account/positions-history",
            params={
                "instType": "SWAP",
                "instId": inst_id,
                "limit": str(max(1, min(int(limit or 50), 100))),
            },
        )
    except Exception as exc:
        logger.info("OKX native position history unavailable: %s", exc)
        return {"supported": False, "positions": []}

    rows = raw.get("data") if isinstance(raw, dict) else []
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or str(row.get("instId") or "") != inst_id:
            continue
        quantity = abs(_number(row.get("closeTotalPos") or row.get("openMaxPos")))
        entry_price = _number(row.get("openAvgPx"))
        exit_price = _number(row.get("closeAvgPx"))
        if quantity <= 0 or entry_price <= 0 or exit_price <= 0:
            continue
        fee = abs(_number(row.get("fee")))
        funding = _number(row.get("fundingFee"))
        gross_pnl = _number(row.get("pnl"))
        realized = row.get("realizedPnl")
        net_pnl = _number(realized) if realized not in (None, "") else gross_pnl + funding - fee
        direction = str(row.get("direction") or row.get("posSide") or "").lower()
        result.append({
            "id": f"{row.get('posId') or 'position'}:{row.get('uTime') or row.get('cTime')}:{direction}",
            "symbol": symbol,
            "side": direction if direction in {"long", "short"} else "long",
            "opened_at": utc_time(row.get("cTime")),
            "closed_at": utc_time(row.get("uTime")),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl": net_pnl,
            "gross_pnl": gross_pnl,
            "fee": fee,
            "funding": funding,
            "source": "exchange",
        })
    result.sort(key=lambda row: str(row.get("closed_at") or ""), reverse=True)
    return {"supported": True, "positions": result[: max(1, int(limit or 50))]}


def binance_position_history(client, symbol, limit=50):
    """Rebuild closed Binance futures legs from exchange trade history."""
    if not isinstance(client, BinanceFuturesClient):
        return {"supported": False, "positions": []}
    native_symbol = to_binance_futures_symbol(symbol)
    try:
        rows = client.get_user_trades(symbol=symbol, limit=1000)
    except Exception as exc:
        logger.info("Binance native trade history unavailable: %s", exc)
        return {"supported": False, "positions": []}

    states = {
        "long": {"quantity": 0.0, "notional": 0.0, "fee": 0.0, "opened_at": None},
        "short": {"quantity": 0.0, "notional": 0.0, "fee": 0.0, "opened_at": None},
    }
    result = []

    def add(side, quantity, price, fee, timestamp):
        state = states[side]
        if state["quantity"] <= 0:
            state["opened_at"] = timestamp
        state["quantity"] += quantity
        state["notional"] += quantity * price
        state["fee"] += fee

    def close(side, quantity, price, fee, gross_pnl, timestamp, row_id):
        state = states[side]
        closing = min(quantity, state["quantity"])
        if closing <= 0:
            return quantity
        before = state["quantity"]
        ratio = closing / before
        entry_price = state["notional"] / before if before > 0 else 0.0
        allocated_open_fee = state["fee"] * ratio
        total_fee = allocated_open_fee + fee * (closing / quantity if quantity > 0 else 0.0)
        allocated_pnl = gross_pnl * (closing / quantity if quantity > 0 else 0.0)
        result.append({
            "id": f"{row_id}:{side}",
            "symbol": symbol,
            "side": side,
            "opened_at": utc_time(state["opened_at"]),
            "closed_at": utc_time(timestamp),
            "entry_price": entry_price,
            "exit_price": price,
            "quantity": closing,
            "pnl": allocated_pnl - total_fee,
            "gross_pnl": allocated_pnl,
            "fee": total_fee,
            "funding": 0.0,
            "source": "exchange",
        })
        remaining_ratio = max(0.0, 1.0 - ratio)
        state["quantity"] -= closing
        state["notional"] *= remaining_ratio
        state["fee"] *= remaining_ratio
        if state["quantity"] <= 1e-15:
            state.update(quantity=0.0, notional=0.0, fee=0.0, opened_at=None)
        return quantity - closing

    ordered = sorted(
        (row for row in (rows or []) if isinstance(row, dict)),
        key=lambda row: (_number(row.get("time")), _number(row.get("id"))),
    )
    for row in ordered:
        if str(row.get("symbol") or "").upper() != native_symbol:
            continue
        quantity = abs(_number(row.get("qty")))
        price = _number(row.get("price"))
        if quantity <= 0 or price <= 0:
            continue
        fee_asset = str(row.get("commissionAsset") or "").upper()
        fee = abs(_number(row.get("commission"))) if fee_asset in {"USDT", "USDC", "BUSD", ""} else 0.0
        gross_pnl = _number(row.get("realizedPnl"))
        side = str(row.get("side") or "").upper()
        position_side = str(row.get("positionSide") or "BOTH").upper()
        timestamp = row.get("time")
        row_id = str(row.get("id") or row.get("orderId") or timestamp)
        if position_side == "LONG":
            if side == "BUY":
                add("long", quantity, price, fee, timestamp)
            else:
                close("long", quantity, price, fee, gross_pnl, timestamp, row_id)
            continue
        if position_side == "SHORT":
            if side == "SELL":
                add("short", quantity, price, fee, timestamp)
            else:
                close("short", quantity, price, fee, gross_pnl, timestamp, row_id)
            continue
        if side == "BUY":
            remainder = close("short", quantity, price, fee, gross_pnl, timestamp, row_id)
            if remainder > 1e-15:
                add("long", remainder, price, fee * (remainder / quantity), timestamp)
        elif side == "SELL":
            remainder = close("long", quantity, price, fee, gross_pnl, timestamp, row_id)
            if remainder > 1e-15:
                add("short", remainder, price, fee * (remainder / quantity), timestamp)

    result.sort(key=lambda row: str(row.get("closed_at") or ""), reverse=True)
    return {"supported": True, "positions": result[: max(1, int(limit or 50))]}


def binance_trade_history(client, symbol, market_type="swap", limit=50):
    """Return exchange-owned Binance fills for the selected instrument."""
    market = str(market_type or "swap").strip().lower()
    if isinstance(client, BinanceFuturesClient) and market == "swap":
        reader = client.get_user_trades
    elif isinstance(client, BinanceSpotClient) and market == "spot":
        reader = client.get_my_trades
    else:
        return {"supported": False, "trades": []}

    native_symbol = to_binance_futures_symbol(symbol)
    try:
        rows = reader(symbol=symbol, limit=max(1, min(int(limit or 50), 1000)))
    except Exception as exc:
        logger.info("Binance native trade history unavailable: %s", exc)
        return {"supported": False, "trades": []}

    net_quantity = 0.0
    normalized = []
    ordered = sorted(
        (row for row in (rows or []) if isinstance(row, dict)),
        key=lambda row: (_number(row.get("time")), _number(row.get("id") or row.get("tradeId"))),
    )
    for row in ordered:
        if str(row.get("symbol") or "").upper() != native_symbol:
            continue
        quantity = abs(_number(row.get("qty") or row.get("quantity")))
        price = _number(row.get("price"))
        if quantity <= 0 or price <= 0:
            continue
        side = str(row.get("side") or ("BUY" if row.get("isBuyer") else "SELL")).strip().lower()
        if side not in {"buy", "sell"}:
            continue
        position_side = str(row.get("positionSide") or "BOTH").strip().upper()
        realized_value = row.get("realizedPnl")
        realized_pnl = None if realized_value in (None, "") else _number(realized_value)
        is_close = False
        close_side = ""
        if market == "swap":
            if position_side == "LONG":
                is_close = side == "sell"
                close_side = "long" if is_close else ""
            elif position_side == "SHORT":
                is_close = side == "buy"
                close_side = "short" if is_close else ""
            else:
                delta = quantity if side == "buy" else -quantity
                if net_quantity > 1e-15 and delta < 0:
                    is_close = True
                    close_side = "long"
                elif net_quantity < -1e-15 and delta > 0:
                    is_close = True
                    close_side = "short"
                net_quantity += delta
                if abs(net_quantity) <= 1e-15:
                    net_quantity = 0.0
            if realized_pnl is not None and abs(realized_pnl) > 1e-15:
                is_close = True
                close_side = close_side or ("short" if side == "buy" else "long")

        commission_ccy = str(row.get("commissionAsset") or "").upper()
        commission = abs(_number(row.get("commission")))
        quote_ccy = str(symbol or "").split("/", 1)[-1].split(":", 1)[0].upper()
        trade_id = str(row.get("id") or row.get("tradeId") or f"{row.get('orderId')}:{row.get('time')}")
        normalized.append({
            "id": f"exchange-{trade_id}",
            "symbol": symbol,
            "side": side,
            "order_type": "fill",
            "amount": quantity * price,
            "price": 0.0,
            "leverage": 0,
            "market_type": market,
            "status": "filled",
            "exchange_order_id": str(row.get("orderId") or ""),
            "client_order_id": str(row.get("clientOrderId") or ""),
            "filled_amount": quantity,
            "avg_fill_price": price,
            "commission": commission,
            "commission_ccy": commission_ccy,
            "commission_quote": commission if not commission_ccy or commission_ccy == quote_ccy else None,
            "fee_status": "confirmed" if commission > 0 else "actual_zero",
            "realized_pnl": realized_pnl if is_close else None,
            "source": "exchange",
            "is_close": is_close,
            "close_side": close_side,
            "created_at": utc_time(row.get("time")),
        })

    normalized.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return {"supported": True, "trades": normalized[: max(1, int(limit or 50))]}


def bybit_position_history(client, symbol, limit=50):
    """Return exchange-owned closed position cycles for Bybit linear contracts."""
    if not isinstance(client, BybitClient) or str(getattr(client, "category", "linear")) != "linear":
        return {"supported": False, "positions": []}
    native_symbol = to_bybit_symbol(symbol)
    try:
        raw = client._signed_request(
            "GET",
            "/v5/position/closed-pnl",
            params={
                "category": "linear",
                "symbol": native_symbol,
                "limit": max(1, min(int(limit or 50), 100)),
            },
        )
    except Exception as exc:
        logger.info("Bybit native position history unavailable: %s", exc)
        return {"supported": False, "positions": []}

    result_data = raw.get("result") if isinstance(raw, dict) else {}
    rows = result_data.get("list") if isinstance(result_data, dict) else []
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or str(row.get("symbol") or "").upper() != native_symbol:
            continue
        quantity = abs(_number(row.get("closedSize") or row.get("qty")))
        entry_price = _number(row.get("avgEntryPrice"))
        exit_price = _number(row.get("avgExitPrice"))
        if quantity <= 0 or entry_price <= 0 or exit_price <= 0:
            continue
        closing_side = str(row.get("side") or "").lower()
        position_side = "long" if closing_side == "sell" else "short"
        fee = abs(_number(row.get("openFee"))) + abs(_number(row.get("closeFee")))
        net_pnl = _number(row.get("closedPnl"))
        result.append({
            "id": f"{row.get('orderId') or row.get('createdTime')}:{position_side}",
            "symbol": symbol,
            "side": position_side,
            "opened_at": None,
            "closed_at": utc_time(row.get("updatedTime") or row.get("createdTime")),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl": net_pnl,
            "gross_pnl": net_pnl + fee,
            "fee": fee,
            "funding": 0.0,
            "source": "exchange",
        })
    result.sort(key=lambda row: str(row.get("closed_at") or ""), reverse=True)
    return {"supported": True, "positions": result[: max(1, int(limit or 50))]}


def htx_position_history(client, symbol, limit=50):
    """Rebuild closed HTX swap legs from exchange match results."""
    if not isinstance(client, HtxClient) or str(getattr(client, "market_type", "swap")) == "spot":
        return {"supported": False, "positions": []}
    contract_code = to_htx_contract_code(symbol)
    endpoint = (
        "/linear-swap-api/v1/swap_matchresults_exact"
        if str(getattr(client, "margin_mode", "cross")) == "isolated"
        else "/linear-swap-api/v1/swap_cross_matchresults_exact"
    )
    try:
        raw = client._swap_private_request_raw(
            "POST",
            endpoint,
            json_body={"contract_code": contract_code, "trade_type": 0, "size": 100},
        )
        multiplier = contract_multiplier(client, "htx", symbol)
    except Exception as exc:
        logger.info("HTX native trade history unavailable: %s", exc)
        return {"supported": False, "positions": []}

    data = raw.get("data") if isinstance(raw, dict) else {}
    rows = data.get("trades") if isinstance(data, dict) else data
    states = {
        "long": {"quantity": 0.0, "notional": 0.0, "fee": 0.0, "opened_at": None},
        "short": {"quantity": 0.0, "notional": 0.0, "fee": 0.0, "opened_at": None},
    }
    result = []

    def add(side, quantity, price, fee, timestamp):
        state = states[side]
        if state["quantity"] <= 0:
            state["opened_at"] = timestamp
        state["quantity"] += quantity
        state["notional"] += quantity * price
        state["fee"] += fee

    def close(side, quantity, price, fee, timestamp, row):
        state = states[side]
        closing = min(quantity, state["quantity"])
        if closing <= 0:
            return
        before = state["quantity"]
        ratio = closing / before
        entry_price = state["notional"] / before if before > 0 else 0.0
        total_fee = state["fee"] * ratio + fee * (closing / quantity if quantity > 0 else 0.0)
        reported = row.get("real_profit")
        if reported in (None, ""):
            reported = row.get("profit")
        gross_pnl = _number(reported) if reported not in (None, "") else (
            (price - entry_price) * closing if side == "long" else (entry_price - price) * closing
        )
        result.append({
            "id": f"{row.get('trade_id') or row.get('match_id') or row.get('id') or timestamp}:{side}",
            "symbol": symbol,
            "side": side,
            "opened_at": utc_time(state["opened_at"]),
            "closed_at": utc_time(timestamp),
            "entry_price": entry_price,
            "exit_price": price,
            "quantity": closing,
            "pnl": gross_pnl - total_fee,
            "gross_pnl": gross_pnl,
            "fee": total_fee,
            "funding": 0.0,
            "source": "exchange",
        })
        remaining_ratio = max(0.0, 1.0 - ratio)
        state["quantity"] -= closing
        state["notional"] *= remaining_ratio
        state["fee"] *= remaining_ratio
        if state["quantity"] <= 1e-15:
            state.update(quantity=0.0, notional=0.0, fee=0.0, opened_at=None)

    ordered = sorted(
        (row for row in (rows or []) if isinstance(row, dict)),
        key=lambda row: _number(row.get("created_at") or row.get("created-at") or row.get("ts")),
    )
    for row in ordered:
        row_contract = str(row.get("contract_code") or row.get("contractCode") or contract_code).upper()
        if row_contract != contract_code:
            continue
        contracts = abs(_number(row.get("trade_volume") or row.get("tradeVolume") or row.get("volume")))
        quantity = contracts * max(multiplier, 0.0)
        price = _number(row.get("trade_price") or row.get("tradePrice") or row.get("price"))
        if quantity <= 0 or price <= 0:
            continue
        fee = abs(_number(row.get("trade_fee") or row.get("tradeFee") or row.get("fee")))
        direction = str(row.get("direction") or "").lower()
        offset = str(row.get("offset") or "").lower()
        timestamp = row.get("created_at") or row.get("created-at") or row.get("ts")
        side = "long" if direction == "buy" else "short"
        if offset == "open":
            add(side, quantity, price, fee, timestamp)
        elif offset == "close":
            close("long" if direction == "sell" else "short", quantity, price, fee, timestamp, row)

    result.sort(key=lambda row: str(row.get("closed_at") or ""), reverse=True)
    return {"supported": True, "positions": result[: max(1, int(limit or 50))]}


def bitget_position_history(client, symbol, limit=50):
    """Return exchange-owned closed position cycles for Bitget futures."""
    resolver = getattr(client, "_resolve_bitget_client", None)
    actual = resolver() if callable(resolver) else client
    native_symbol = to_bitget_um_symbol(symbol)
    quote = str(symbol or "").split("/", 1)[-1].split(":", 1)[0].upper()
    product_type = "USDC-FUTURES" if quote == "USDC" else "USDT-FUTURES"

    try:
        if isinstance(actual, BitgetClassicMixClient):
            raw = actual._signed_request(
                "GET",
                "/api/v2/mix/position/history-position",
                params={
                    "symbol": native_symbol,
                    "productType": product_type,
                    "limit": max(1, min(int(limit or 50), 100)),
                },
            )
        elif isinstance(actual, BitgetMixClient):
            raw = actual._signed_request(
                "GET",
                "/api/v3/position/history-position",
                params={
                    "symbol": native_symbol,
                    "category": product_type,
                    "limit": max(1, min(int(limit or 50), 100)),
                },
            )
        else:
            return {"supported": False, "positions": []}
    except Exception as exc:
        logger.info("Bitget native position history unavailable: %s", exc)
        return {"supported": False, "positions": []}

    data = raw.get("data") if isinstance(raw, dict) else {}
    rows = data.get("list") if isinstance(data, dict) else []
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        row_symbol = str(row.get("symbol") or row.get("instId") or "").upper()
        if row_symbol and row_symbol != native_symbol:
            continue
        quantity = abs(_number(
            row.get("closeTotalPos")
            or row.get("closeSize")
            or row.get("openTotalPos")
            or row.get("openSize")
        ))
        entry_price = _number(row.get("openAvgPrice") or row.get("openPriceAvg"))
        exit_price = _number(row.get("closeAvgPrice") or row.get("closePriceAvg"))
        if quantity <= 0 or entry_price <= 0 or exit_price <= 0:
            continue
        open_fee = abs(_number(row.get("openFee")))
        close_fee = abs(_number(row.get("closeFee")))
        settle_fee = abs(_number(row.get("settleFee")))
        fee = open_fee + close_fee + settle_fee
        gross_pnl = _number(row.get("pnl") or row.get("achievedProfits"))
        funding = _number(row.get("totalFunding"))
        cash_dividend = _number(row.get("cashDividend"))
        net_value = row.get("netProfit")
        net_pnl = (
            _number(net_value)
            if net_value not in (None, "")
            else gross_pnl + funding + cash_dividend - fee
        )
        result.append({
            "id": str(row.get("positionId") or row.get("posId") or f"{row.get('utime') or row.get('uTime')}:{row.get('holdSide')}"),
            "symbol": symbol,
            "side": str(row.get("holdSide") or row.get("positionSide") or "").lower(),
            "opened_at": utc_time(row.get("ctime") or row.get("cTime")),
            "closed_at": utc_time(row.get("utime") or row.get("uTime")),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl": net_pnl,
            "gross_pnl": gross_pnl,
            "fee": fee,
            "funding": funding,
            "source": "exchange",
        })
    result.sort(key=lambda row: str(row.get("closed_at") or ""), reverse=True)
    return {"supported": True, "positions": result[: max(1, int(limit or 50))]}


def exchange_position_history(client, symbol, limit=50):
    """Return the first supported native position history for an exchange client."""
    readers = (
        gate_position_history,
        bitget_position_history,
        okx_position_history,
        binance_position_history,
        bybit_position_history,
        htx_position_history,
    )
    for reader in readers:
        result = reader(client, symbol, limit=limit)
        if result.get("supported"):
            return result
    return {"supported": False, "positions": []}


def gate_native_protection_fills(client, symbol, limit=100):
    """Return filled Gate price orders created by exchange-native protection."""
    if not isinstance(client, GateUsdtFuturesClient):
        return {"supported": False, "fills": []}

    contract = to_gate_currency_pair(symbol)
    rows = client._signed_request(
        "GET",
        "/api/v4/futures/usdt/my_trades",
        params={"contract": contract, "limit": max(1, min(int(limit or 100), 1000))},
    )
    grouped = {}
    for row in rows or []:
        if str(row.get("contract") or "") != contract:
            continue
        text = str(row.get("text") or "").strip()
        if not text.startswith("ao-"):
            continue
        order_id = str(row.get("order_id") or "").strip()
        if not order_id:
            continue
        try:
            contracts = float(row.get("size") or 0)
            price = float(row.get("price") or 0)
            fee = abs(float(row.get("fee") or 0))
            created_at = float(row.get("create_time") or row.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if contracts == 0 or price <= 0:
            continue
        item = grouped.setdefault(order_id, {
            "exchange_order_id": order_id,
            "client_order_id": text,
            "contracts": 0.0,
            "contract_weight": 0.0,
            "weighted_price": 0.0,
            "commission": 0.0,
            "created_at": 0.0,
            "exchange_fill_ids": [],
        })
        item["contracts"] += contracts
        item["contract_weight"] += abs(contracts)
        item["weighted_price"] += abs(contracts) * price
        item["commission"] += fee
        item["created_at"] = max(item["created_at"], created_at)
        fill_id = str(row.get("id") or "").strip()
        if fill_id:
            item["exchange_fill_ids"].append(fill_id)

    fills = []
    for item in grouped.values():
        contracts = float(item.pop("contracts") or 0)
        weight = float(item.pop("contract_weight") or 0)
        weighted_price = float(item.pop("weighted_price") or 0)
        created_at = float(item.pop("created_at") or 0)
        if contracts == 0 or weight <= 0:
            continue
        quantity = client.contracts_signed_to_base_qty(
            contract=contract,
            contracts_signed=contracts,
        )
        if quantity <= 0:
            continue
        side = "sell" if contracts < 0 else "buy"
        fills.append({
            **item,
            "symbol": symbol,
            "side": side,
            "close_side": "long" if side == "sell" else "short",
            "filled_amount": quantity,
            "avg_fill_price": weighted_price / weight,
            "commission_ccy": "USDT",
            "created_at": utc_time(created_at),
        })
    fills.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return {"supported": True, "fills": fills}


def gate_protection_orders(client, symbol):
    if not isinstance(client, GateUsdtFuturesClient):
        return {"supported": False, "orders": []}
    contract = to_gate_currency_pair(symbol)
    rows = client._signed_request("GET", "/api/v4/futures/usdt/price_orders", params={"status": "open", "contract": contract, "limit": 100})
    orders = []
    for row in rows or []:
        initial = row.get("initial") or {}
        trigger = row.get("trigger") or {}
        reducing = initial.get("is_reduce_only", initial.get("reduce_only", False))
        if initial.get("contract") != contract or not reducing:
            continue
        side = "long" if float(initial.get("size") or initial.get("amount") or 0) < 0 else "short"
        if initial.get("auto_size") in ("close_long", "close_short"):
            side = initial["auto_size"].removeprefix("close_")
        orders.append({"id": str(row.get("id_string") or row["id"]), "side": side, "price": float(trigger.get("price") or 0),
                       "kind": "tp" if (int(trigger.get("rule") or 0) == 1) == (side == "long") else "sl"})
    return {"supported": True, "orders": orders}


def validate_exit_price(side, kind, price, reference):
    if not all(isfinite(v) and v > 0 for v in (price, reference)):
        raise ValueError("quickTrade.workflow.invalidExitPrice")
    rises = (side == "long") == (kind == "tp")
    if (rises and price <= reference) or (not rises and price >= reference):
        raise ValueError("quickTrade.workflow.invalidExitPrice")


def _raw_dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _metadata(row):
    raw = _raw_dict(row.get("raw_result"))
    value = raw.get("_quick_trade")
    return value if isinstance(value, dict) else {}


def _component_ids(raw):
    result = set()
    payload = _raw_dict(raw)
    for key in ("exchange_close", "gate_auto_order"):
        section = payload.get(key)
        if not isinstance(section, dict):
            continue
        for value in section.get("exchange_fill_ids") or []:
            if str(value or "").strip():
                result.add(str(value).strip())
        for component in section.get("components") or []:
            if not isinstance(component, dict):
                continue
            for field in ("exchange_fill_id", "exchange_order_id"):
                value = str(component.get(field) or "").strip()
                if value:
                    result.add(value)
    return result


def tracked_quick_trade_legs(rows, market_type="swap"):
    """Build unresolved account legs and recorded exchange references."""
    market = str(market_type or "swap").strip().lower()
    legs = {
        "long": {"side": "long", "quantity": 0.0, "entry_price": 0.0, "opened_at": None, "leverage": 1},
        "short": {"side": "short", "quantity": 0.0, "entry_price": 0.0, "opened_at": None, "leverage": 1},
    }
    recorded_ids = set()
    ordered = sorted(rows or [], key=lambda row: str(row.get("created_at") or ""))
    for row in ordered:
        if str(row.get("status") or "").lower() != "filled":
            continue
        order_id = str(row.get("exchange_order_id") or "").strip()
        if order_id:
            recorded_ids.add(order_id)
        recorded_ids.update(_component_ids(row.get("raw_result")))
        qty = abs(_number(row.get("filled_amount") or row.get("amount")))
        if qty <= 0:
            continue
        side = str(row.get("side") or "").strip().lower()
        meta = _metadata(row)
        is_close = bool(meta.get("is_close"))
        if market == "spot":
            position_side = "long"
            is_close = is_close or side == "sell"
        else:
            position_side = str(meta.get("close_side") or "").lower() if is_close else ("long" if side == "buy" else "short")
        if position_side not in legs:
            continue
        leg = legs[position_side]
        if is_close:
            leg["quantity"] = max(0.0, leg["quantity"] - qty)
            if leg["quantity"] <= 1e-12:
                leg.update(quantity=0.0, entry_price=0.0, opened_at=None, leverage=1)
            continue
        previous = leg["quantity"]
        price = _number(row.get("avg_fill_price") or row.get("price"))
        leg["quantity"] = previous + qty
        if price > 0:
            leg["entry_price"] = (
                (leg["entry_price"] * previous + price * qty) / leg["quantity"]
                if previous > 0 else price
            )
        if previous <= 1e-12:
            leg["opened_at"] = row.get("created_at")
        leg["leverage"] = int(_number(row.get("leverage")) or 1)
    return {
        "legs": [dict(leg) for leg in legs.values() if leg["quantity"] > 1e-12],
        "recorded_ids": recorded_ids,
    }


def quick_trade_position_history(rows, symbol):
    """Build derivative close history when a venue has no native position-history API."""
    states = {
        "long": {"quantity": 0.0, "entry_price": 0.0, "opened_at": None, "entry_fee": 0.0},
        "short": {"quantity": 0.0, "entry_price": 0.0, "opened_at": None, "entry_fee": 0.0},
    }
    result = []
    ordered = sorted(rows or [], key=lambda row: str(row.get("created_at") or ""))
    for row in ordered:
        if str(row.get("status") or "").lower() != "filled":
            continue
        qty = abs(_number(row.get("filled_amount") or row.get("amount")))
        price = _number(row.get("avg_fill_price") or row.get("price"))
        if qty <= 0 or price <= 0:
            continue
        meta = _metadata(row)
        side = str(row.get("side") or "").lower()
        if not meta.get("is_close"):
            position_side = "long" if side == "buy" else "short"
            state = states[position_side]
            previous = state["quantity"]
            state["quantity"] = previous + qty
            state["entry_price"] = (
                (state["entry_price"] * previous + price * qty) / state["quantity"]
                if previous > 0 else price
            )
            if previous <= 1e-12:
                state["opened_at"] = row.get("created_at")
            state["entry_fee"] += abs(_number(row.get("commission_quote") or row.get("commission")))
            continue
        position_side = str(meta.get("close_side") or ("long" if side == "sell" else "short")).lower()
        if position_side not in states:
            continue
        state = states[position_side]
        closed_qty = min(qty, state["quantity"])
        if closed_qty <= 1e-12:
            continue
        fee_ratio = min(1.0, closed_qty / state["quantity"])
        entry_fee = state["entry_fee"] * fee_ratio
        close_fee = abs(_number(row.get("commission_quote") or row.get("commission")))
        gross = (price - state["entry_price"]) * closed_qty
        if position_side == "short":
            gross = -gross
        total_fee = entry_fee + close_fee
        result.append({
            "id": f"quick-trade-{row.get('id')}-{position_side}",
            "symbol": symbol,
            "side": position_side,
            "opened_at": state["opened_at"],
            "closed_at": row.get("created_at"),
            "entry_price": state["entry_price"],
            "exit_price": price,
            "quantity": closed_qty,
            "pnl": gross - total_fee,
            "gross_pnl": gross,
            "fee": total_fee,
            "funding": 0.0,
            "source": "quick_trade_reconciled",
        })
        state["quantity"] = max(0.0, state["quantity"] - closed_qty)
        state["entry_fee"] = max(0.0, state["entry_fee"] - entry_fee)
        if state["quantity"] <= 1e-12:
            state.update(quantity=0.0, entry_price=0.0, opened_at=None, entry_fee=0.0)
    result.sort(key=lambda row: str(row.get("closed_at") or ""), reverse=True)
    return {"supported": True, "positions": result[:50]}
