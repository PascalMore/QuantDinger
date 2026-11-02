"""Normalize account-owned exchange fills for Quick Trade history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List

from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_classic import BitgetClassicMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bitget_spot_classic import BitgetClassicSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.fill_accounting import contract_multiplier
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient
from app.services.live_trading.symbols import (
    to_bitget_um_symbol,
    to_bybit_symbol,
    to_gate_currency_pair,
    to_htx_contract_code,
    to_okx_spot_inst_id,
    to_okx_swap_inst_id,
)
from app.services.quick_trade.credentials import build_exchange_config, create_exchange_client
from app.utils.logger import get_logger


logger = get_logger(__name__)

ACTIVE_ORDER_STATUSES = frozenset({
    "new", "open", "pending", "submitted", "partially_filled",
    "partially-filled", "accepted", "cancel_pending",
})


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _utc_time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000_000:
        timestamp /= 1_000_000.0
    elif timestamp > 10_000_000_000:
        timestamp /= 1000.0
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _quote_currency(symbol: str) -> str:
    return str(symbol or "").split("/", 1)[-1].split(":", 1)[0].upper()


def _native_symbol(symbol: str) -> str:
    return str(symbol or "").replace("/", "").replace("-", "").split(":", 1)[0].upper()


def _fee_detail(value: Any, default_ccy: str = "") -> tuple[float, str]:
    rows = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    fees: Dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        currency = str(
            row.get("feeCoin")
            or row.get("feeCcy")
            or row.get("feeCurrency")
            or default_ccy
        ).upper()
        raw_fee = row.get("totalFee")
        if raw_fee in (None, ""):
            raw_fee = row.get("fee")
        amount = abs(_number(raw_fee))
        if currency and amount > 0:
            fees[currency] = fees.get(currency, 0.0) + amount
    if not fees:
        return 0.0, str(default_ccy or "").upper()
    if len(fees) == 1:
        currency, amount = next(iter(fees.items()))
        return amount, currency
    return sum(fees.values()), "MIXED"


def _classify_fill(
    *,
    market_type: str,
    side: str,
    position_side: str = "",
    trade_side: str = "",
    close_quantity: float = 0.0,
    realized_pnl: float | None = None,
    net_quantity: float = 0.0,
) -> tuple[bool, str, float]:
    if market_type == "spot":
        return side == "sell", "long" if side == "sell" else "", net_quantity

    normalized_position = str(position_side or "").strip().lower()
    normalized_trade = str(trade_side or "").strip().lower()
    if "close" in normalized_trade or "reduce" in normalized_trade:
        close_side = normalized_position if normalized_position in {"long", "short"} else ("long" if side == "sell" else "short")
        return True, close_side, net_quantity
    if "open" in normalized_trade:
        return False, "", net_quantity
    if normalized_position == "long":
        return side == "sell", "long" if side == "sell" else "", net_quantity
    if normalized_position == "short":
        return side == "buy", "short" if side == "buy" else "", net_quantity
    if close_quantity > 0:
        return True, "long" if side == "sell" else "short", net_quantity

    signed_delta = 1.0 if side == "buy" else -1.0
    is_close = (net_quantity > 1e-15 and signed_delta < 0) or (net_quantity < -1e-15 and signed_delta > 0)
    close_side = "long" if net_quantity > 0 and is_close else "short" if net_quantity < 0 and is_close else ""
    if realized_pnl is not None and abs(realized_pnl) > 1e-15:
        is_close = True
        close_side = close_side or ("short" if side == "buy" else "long")
    return is_close, close_side, net_quantity


def _record(
    *,
    exchange: str,
    fill_id: Any,
    symbol: str,
    side: str,
    market_type: str,
    quantity: float,
    price: float,
    commission: float = 0.0,
    commission_ccy: str = "",
    realized_pnl: float | None = None,
    is_close: bool = False,
    close_side: str = "",
    order_id: Any = "",
    client_order_id: Any = "",
    created_at: Any = None,
) -> Dict[str, Any] | None:
    fill_key = str(fill_id or "").strip()
    quantity_value = abs(_number(quantity))
    price_value = _number(price)
    timestamp = _utc_time(created_at)
    if not fill_key or quantity_value <= 0 or price_value <= 0 or not timestamp:
        return None
    quote = _quote_currency(symbol)
    fee_currency = str(commission_ccy or "").strip().upper()
    fee_value = abs(_number(commission))
    return {
        "id": f"exchange-{exchange}-{fill_key}",
        "symbol": symbol,
        "side": side,
        "order_type": "fill",
        "amount": quantity_value * price_value,
        "price": 0.0,
        "leverage": 0,
        "market_type": market_type,
        "status": "filled",
        "exchange_order_id": str(order_id or ""),
        "client_order_id": str(client_order_id or ""),
        "filled_amount": quantity_value,
        "avg_fill_price": price_value,
        "commission": fee_value,
        "commission_ccy": fee_currency,
        "commission_quote": fee_value if not fee_currency or fee_currency == quote else None,
        "fee_status": "confirmed" if fee_value > 0 else "actual_zero",
        "realized_pnl": realized_pnl if is_close else None,
        "source": "exchange",
        "is_close": bool(is_close),
        "close_side": close_side if is_close else "",
        "created_at": timestamp,
    }


def _normalize_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    limit: int,
    offset: int,
) -> List[Dict[str, Any]]:
    normalized = [row for row in rows if isinstance(row, dict)]
    normalized.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    start = max(0, int(offset or 0))
    return normalized[start:start + max(1, int(limit or 50))]


def gate_trade_history(client: Any, symbol: str, market_type: str, limit: int, offset: int = 0):
    market = "spot" if str(market_type).lower() == "spot" else "swap"
    pair = to_gate_currency_pair(symbol)
    if market == "spot" and isinstance(client, GateSpotClient):
        rows = client._order_trade_rows(
            "/api/v4/spot/my_trades",
            {"currency_pair": pair, "limit": min(1000, max(100, limit + offset))},
        )
    elif market == "swap" and isinstance(client, GateUsdtFuturesClient):
        rows = client._signed_request(
            "GET",
            "/api/v4/futures/usdt/my_trades",
            params={"contract": pair, "limit": min(1000, max(100, limit + offset)), "offset": 0},
        )
    else:
        return {"supported": False, "trades": []}

    output = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        row_symbol = str(row.get("currency_pair") or row.get("contract") or pair).upper()
        if row_symbol != pair:
            continue
        if market == "spot":
            side = str(row.get("side") or "").lower()
            quantity = abs(_number(row.get("amount")))
            close_quantity = quantity if side == "sell" else 0.0
        else:
            contracts = _number(row.get("size"))
            side = "buy" if contracts > 0 else "sell"
            quantity = abs(client.contracts_signed_to_base_qty(contract=pair, contracts_signed=abs(contracts)))
            close_contracts = abs(_number(row.get("close_size")))
            close_quantity = abs(client.contracts_signed_to_base_qty(contract=pair, contracts_signed=close_contracts)) if close_contracts > 0 else 0.0
        if side not in {"buy", "sell"}:
            continue
        realized_raw = row.get("pnl")
        realized = None if realized_raw in (None, "") else _number(realized_raw)
        is_close, close_side, _ = _classify_fill(
            market_type=market,
            side=side,
            close_quantity=close_quantity,
            realized_pnl=realized,
        )
        item = _record(
            exchange="gate",
            fill_id=row.get("id") or row.get("trade_id"),
            symbol=symbol,
            side=side,
            market_type=market,
            quantity=quantity,
            price=_number(row.get("price")),
            commission=_number(row.get("fee")),
            commission_ccy=row.get("fee_currency") or (_quote_currency(symbol) if market == "swap" else ""),
            realized_pnl=realized,
            is_close=is_close,
            close_side=close_side,
            order_id=row.get("order_id") or row.get("orderId"),
            client_order_id=row.get("text") or row.get("client_order_id"),
            created_at=row.get("create_time_ms") or row.get("time_ms") or row.get("create_time") or row.get("time"),
        )
        if item:
            output.append(item)
    return {"supported": True, "trades": _normalize_rows(output, limit=limit, offset=offset)}


def okx_trade_history(client: Any, symbol: str, market_type: str, limit: int, offset: int = 0):
    if not isinstance(client, OkxClient):
        return {"supported": False, "trades": []}
    market = "spot" if str(market_type).lower() == "spot" else "swap"
    inst_type = "SPOT" if market == "spot" else "SWAP"
    inst_id = to_okx_spot_inst_id(symbol) if market == "spot" else to_okx_swap_inst_id(symbol)
    raw = client._signed_request(
        "GET",
        "/api/v5/trade/fills-history",
        params={"instType": inst_type, "instId": inst_id, "limit": str(min(100, max(1, limit + offset)))},
    )
    rows = raw.get("data") if isinstance(raw, dict) else []
    multiplier = contract_multiplier(client, "okx", symbol) if market == "swap" else 1.0
    output = []
    net_quantity = 0.0
    ordered = sorted(
        (row for row in (rows or []) if isinstance(row, dict)),
        key=lambda row: _number(row.get("fillTime") or row.get("ts")),
    )
    for row in ordered:
        if str(row.get("instId") or "") != inst_id:
            continue
        side = str(row.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            continue
        quantity = abs(_number(row.get("fillSz"))) * max(multiplier, 0.0)
        realized_raw = row.get("fillPnl")
        realized = None if realized_raw in (None, "") else _number(realized_raw)
        is_close, close_side, _ = _classify_fill(
            market_type=market,
            side=side,
            position_side=row.get("posSide"),
            realized_pnl=realized,
            net_quantity=net_quantity,
        )
        if market == "swap" and str(row.get("posSide") or "net").lower() in {"", "net"}:
            net_quantity += quantity if side == "buy" else -quantity
            if abs(net_quantity) <= 1e-15:
                net_quantity = 0.0
        item = _record(
            exchange="okx",
            fill_id=row.get("tradeId") or row.get("fillId"),
            symbol=symbol,
            side=side,
            market_type=market,
            quantity=quantity,
            price=_number(row.get("fillPx")),
            commission=_number(row.get("fee") or row.get("fillFee")),
            commission_ccy=row.get("feeCcy") or row.get("fillFeeCcy") or (_quote_currency(symbol) if market == "swap" else ""),
            realized_pnl=realized,
            is_close=is_close,
            close_side=close_side,
            order_id=row.get("ordId") or row.get("orderId"),
            client_order_id=row.get("clOrdId") or row.get("algoClOrdId"),
            created_at=row.get("fillTime") or row.get("ts"),
        )
        if item:
            output.append(item)
    return {"supported": True, "trades": _normalize_rows(output, limit=limit, offset=offset)}


def bitget_trade_history(client: Any, symbol: str, market_type: str, limit: int, offset: int = 0):
    resolver = getattr(client, "_resolve_bitget_client", None)
    actual = resolver() if callable(resolver) else client
    market = "spot" if str(market_type).lower() == "spot" else "swap"
    native = to_bitget_um_symbol(symbol)
    quote = _quote_currency(symbol)
    product_type = "USDC-FUTURES" if quote == "USDC" else "USDT-FUTURES"
    page_limit = min(100, max(1, limit + offset))
    if isinstance(actual, (BitgetMixClient, BitgetSpotClient)):
        category = "SPOT" if market == "spot" else product_type
        rows = []
        cursor = ""
        required = max(1, limit + offset)
        target_symbol = _native_symbol(symbol)
        for _ in range(10):
            params = {"category": category, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            raw = actual._signed_request("GET", "/api/v3/trade/fills", params=params)
            data = raw.get("data") if isinstance(raw, dict) else {}
            page = data.get("list") if isinstance(data, dict) else []
            if not isinstance(page, list) or not page:
                break
            rows.extend(page)
            matched = sum(
                1
                for row in rows
                if isinstance(row, dict)
                and _native_symbol(row.get("symbol") or "") == target_symbol
            )
            next_cursor = str(data.get("cursor") or "") if isinstance(data, dict) else ""
            if matched >= required or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
    elif isinstance(actual, BitgetClassicMixClient) and market == "swap":
        raw = actual._signed_request(
            "GET",
            "/api/v2/mix/order/fills",
            params={"symbol": native, "productType": product_type, "limit": page_limit},
        )
        data = raw.get("data") if isinstance(raw, dict) else {}
        rows = data.get("fillList") if isinstance(data, dict) else []
    elif isinstance(actual, BitgetClassicSpotClient) and market == "spot":
        raw = actual._signed_request(
            "GET",
            "/api/v2/spot/trade/fills",
            params={"symbol": native, "limit": page_limit},
        )
        rows = raw.get("data") if isinstance(raw, dict) else []
    else:
        return {"supported": False, "trades": []}

    output = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        row_symbol = _native_symbol(row.get("symbol") or symbol)
        if row_symbol != _native_symbol(symbol):
            continue
        side = str(row.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            continue
        fee, fee_ccy = _fee_detail(row.get("feeDetail"), quote if market == "swap" else "")
        if fee <= 0:
            fee = abs(_number(row.get("fee") or row.get("fillFee") or row.get("totalFee")))
            fee_ccy = str(row.get("feeCoin") or row.get("fillFeeCoin") or fee_ccy).upper()
        realized_raw = row.get("execPnl")
        if realized_raw in (None, ""):
            realized_raw = row.get("profit")
        realized = None if realized_raw in (None, "") else _number(realized_raw)
        is_close, close_side, _ = _classify_fill(
            market_type=market,
            side=side,
            position_side=row.get("posSide") or row.get("holdSide"),
            trade_side=row.get("tradeSide"),
            realized_pnl=realized,
        )
        item = _record(
            exchange="bitget",
            fill_id=row.get("execId") or row.get("tradeId") or row.get("fillId"),
            symbol=symbol,
            side=side,
            market_type=market,
            quantity=_number(row.get("execQty") or row.get("baseVolume") or row.get("size") or row.get("qty")),
            price=_number(row.get("execPrice") or row.get("fillPrice") or row.get("price")),
            commission=fee,
            commission_ccy=fee_ccy,
            realized_pnl=realized,
            is_close=is_close,
            close_side=close_side,
            order_id=row.get("orderId"),
            client_order_id=row.get("clientOid") or row.get("clientOrderId"),
            created_at=row.get("createdTime") or row.get("cTime") or row.get("uTime"),
        )
        if item:
            output.append(item)
    return {"supported": True, "trades": _normalize_rows(output, limit=limit, offset=offset)}


def bybit_trade_history(client: Any, symbol: str, market_type: str, limit: int, offset: int = 0):
    if not isinstance(client, BybitClient):
        return {"supported": False, "trades": []}
    market = "spot" if str(market_type).lower() == "spot" else "swap"
    expected_category = "spot" if market == "spot" else "linear"
    if str(getattr(client, "category", expected_category)).lower() != expected_category:
        return {"supported": False, "trades": []}
    raw = client.get_executions(symbol=symbol)
    result = raw.get("result") if isinstance(raw, dict) else {}
    rows = result.get("list") if isinstance(result, dict) else []
    output = []
    net_quantity = 0.0
    ordered = sorted(
        (row for row in (rows or []) if isinstance(row, dict)),
        key=lambda row: _number(row.get("execTime")),
    )
    for row in ordered:
        if _native_symbol(row.get("symbol") or "") != _native_symbol(symbol):
            continue
        if str(row.get("execType") or "Trade") not in {"", "Trade"}:
            continue
        side = str(row.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            continue
        quantity = abs(_number(row.get("execQty")))
        position_idx = int(_number(row.get("positionIdx")))
        position_side = "long" if position_idx == 1 else "short" if position_idx == 2 else ""
        close_quantity = abs(_number(row.get("closedSize")))
        realized_raw = row.get("closedPnl")
        if realized_raw in (None, ""):
            realized_raw = row.get("execPnl")
        realized = None if realized_raw in (None, "") else _number(realized_raw)
        is_close, close_side, _ = _classify_fill(
            market_type=market,
            side=side,
            position_side=position_side,
            close_quantity=close_quantity,
            realized_pnl=realized,
            net_quantity=net_quantity,
        )
        if market == "swap" and position_idx == 0:
            net_quantity += quantity if side == "buy" else -quantity
            if abs(net_quantity) <= 1e-15:
                net_quantity = 0.0
        item = _record(
            exchange="bybit",
            fill_id=row.get("execId"),
            symbol=symbol,
            side=side,
            market_type=market,
            quantity=quantity,
            price=_number(row.get("execPrice")),
            commission=_number(row.get("execFee")),
            commission_ccy=row.get("feeCurrency") or (_quote_currency(symbol) if market == "swap" else ""),
            realized_pnl=realized,
            is_close=is_close,
            close_side=close_side,
            order_id=row.get("orderId"),
            client_order_id=row.get("orderLinkId"),
            created_at=row.get("execTime"),
        )
        if item:
            output.append(item)
    return {"supported": True, "trades": _normalize_rows(output, limit=limit, offset=offset)}


def htx_trade_history(client: Any, symbol: str, market_type: str, limit: int, offset: int = 0):
    if not isinstance(client, HtxClient):
        return {"supported": False, "trades": []}
    market = "spot" if str(market_type).lower() == "spot" else "swap"
    if market == "spot":
        native = str(symbol or "").replace("/", "").replace("-", "").lower()
        raw = client._spot_private_request(
            "GET",
            "/v1/order/matchresults",
            params={"symbol": native, "size": min(100, max(1, limit + offset))},
        )
        rows = raw.get("data") if isinstance(raw, dict) else []
        multiplier = 1.0
    else:
        native = to_htx_contract_code(symbol)
        endpoint = (
            "/linear-swap-api/v3/swap_matchresults_exact"
            if str(getattr(client, "margin_mode", "cross")) == "isolated"
            else "/linear-swap-api/v3/swap_cross_matchresults_exact"
        )
        request_body: Dict[str, Any] = {"contract": native, "trade_type": 0, "direct": "prev"}
        rows = []
        required = max(1, limit + offset)
        for _ in range(10):
            raw = client._swap_private_request_raw("POST", endpoint, json_body=request_body)
            data = raw.get("data") if isinstance(raw, dict) else []
            if isinstance(data, dict):
                page = data.get("trades") or data.get("data") or []
                next_id = data.get("next_id") or raw.get("next_id")
                remaining = data.get("remain_size")
            else:
                page = data
                next_id = raw.get("next_id") if isinstance(raw, dict) else None
                remaining = raw.get("remain_size") if isinstance(raw, dict) else None
            if isinstance(page, list):
                rows.extend(page)
            previous_id = request_body.get("from_id")
            if len(rows) >= required or not next_id or str(next_id) == str(previous_id):
                break
            if remaining not in (None, "") and _number(remaining) <= 0:
                break
            request_body["from_id"] = next_id
        multiplier = contract_multiplier(client, "htx", symbol)

    output = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        row_symbol = str(row.get("symbol") or row.get("contract_code") or native)
        if _native_symbol(row_symbol) != _native_symbol(symbol):
            continue
        direction = str(row.get("direction") or row.get("type") or "").lower()
        side = "sell" if "sell" in direction else "buy" if "buy" in direction else direction
        if side not in {"buy", "sell"}:
            continue
        quantity = abs(_number(row.get("trade_volume") or row.get("filled-amount") or row.get("volume")))
        if market == "swap":
            quantity *= max(multiplier, 0.0)
        price = _number(row.get("trade_price") or row.get("price"))
        if price <= 0 and quantity > 0:
            price = _number(row.get("filled-cash-amount")) / quantity
        realized_raw = row.get("real_profit")
        if realized_raw in (None, ""):
            realized_raw = row.get("profit")
        realized = None if realized_raw in (None, "") else _number(realized_raw)
        is_close, close_side, _ = _classify_fill(
            market_type=market,
            side=side,
            trade_side=row.get("offset"),
            realized_pnl=realized,
        )
        item = _record(
            exchange="htx",
            fill_id=row.get("id") or row.get("trade_id") or row.get("match-id"),
            symbol=symbol,
            side=side,
            market_type=market,
            quantity=quantity,
            price=price,
            commission=_number(row.get("trade_fee") or row.get("filled-fees") or row.get("fee")),
            commission_ccy=row.get("fee-currency") or row.get("fee_asset") or (_quote_currency(symbol) if market == "swap" else ""),
            realized_pnl=realized,
            is_close=is_close,
            close_side=close_side,
            order_id=row.get("order_id_str") or row.get("order_id") or row.get("order-id") or row.get("orderId"),
            client_order_id=row.get("client_order_id") or row.get("client-order-id"),
            created_at=row.get("create_date") or row.get("created_at") or row.get("created-at") or row.get("ts"),
        )
        if item:
            output.append(item)
    return {"supported": True, "trades": _normalize_rows(output, limit=limit, offset=offset)}


def exchange_trade_history(
    client: Any,
    symbol: str,
    *,
    market_type: str = "swap",
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Return authoritative exchange fills for all supported crypto venues."""
    from app.services.quick_trade.position_activity import binance_trade_history

    readers: tuple[Callable[..., Dict[str, Any]], ...] = (
        lambda current, instrument, market, count, start: binance_trade_history(
            current,
            instrument,
            market_type=market,
            limit=count + start,
        ),
        gate_trade_history,
        okx_trade_history,
        bitget_trade_history,
        bybit_trade_history,
        htx_trade_history,
    )
    for reader in readers:
        try:
            result = reader(client, symbol, market_type, limit, offset)
        except Exception as exc:
            logger.info(
                "Native exchange trade history unavailable client=%s symbol=%s: %s",
                type(client).__name__,
                symbol,
                exc,
            )
            return {"supported": False, "trades": [], "error": type(exc).__name__}
        if result.get("supported"):
            if reader is readers[0]:
                result["trades"] = _normalize_rows(result.get("trades") or [], limit=limit, offset=offset)
            return result
    return {"supported": False, "trades": []}


def resolve_trade_history(
    local_trades: Iterable[Dict[str, Any]],
    user_id: int,
    credential_id: int,
    symbol: str,
    market_type: str,
    limit: int,
    offset: int,
) -> Dict[str, Any]:
    """Prefer authoritative exchange fills while retaining active local orders."""
    local_rows = list(local_trades)
    fallback = {"trades": local_rows, "source": "local_fallback"}
    if credential_id <= 0 or not symbol or market_type not in {"spot", "swap"}:
        return fallback
    try:
        config = build_exchange_config(credential_id, user_id, {"market_type": market_type})
        client = create_exchange_client(config, market_type=market_type)
        exchange_history = exchange_trade_history(
            client,
            symbol,
            market_type=market_type,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.info("Native exchange trade history unavailable: %s", exc)
        return fallback
    if not exchange_history.get("supported"):
        return fallback
    pending_orders = [
        trade for trade in local_rows
        if str(trade.get("status") or "").strip().lower() in ACTIVE_ORDER_STATUSES
    ]
    trades = pending_orders + list(exchange_history.get("trades") or [])
    trades.sort(key=lambda trade: str(trade.get("created_at") or ""), reverse=True)
    return {"trades": trades, "source": "exchange"}
