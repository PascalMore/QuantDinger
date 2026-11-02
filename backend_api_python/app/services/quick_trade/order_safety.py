"""Execution safety helpers shared by Quick Trade routes."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from flask import g, jsonify

from app.services.live_trading.execution_lock import (
    TradeExecutionBusy,
    trade_execution_key,
    trade_execution_lock,
)
from app.services.pending_orders.order_quantities import exchange_quantity_snapshot
from app.services.quick_trade.symbols import symbols_match


def quick_trade_execution_key(body: Optional[Dict[str, Any]], *, user_id: int) -> str:
    payload = body or {}
    market_type = str(payload.get("market_type") or "").strip().lower()
    if market_type in {"future", "futures", "perp", "perpetual"}:
        market_type = "swap"
    if market_type not in {"spot", "swap"}:
        try:
            leverage = int(payload.get("leverage") or 1)
        except (TypeError, ValueError):
            leverage = 1
        market_type = "swap" if leverage > 1 else "spot"
    return trade_execution_key(
        user_id=int(user_id or 0),
        credential_id=int(payload.get("credential_id") or 0),
        market_type=market_type,
        symbol=str(payload.get("symbol") or ""),
    )


def serialized_quick_trade_order(func):
    @wraps(func)
    def wrapped(body, *args, **kwargs):
        key = quick_trade_execution_key(
            body,
            user_id=int(getattr(g, "user_id", 0) or 0),
        )
        try:
            with trade_execution_lock(key, timeout_sec=3.0):
                return func(body, *args, **kwargs)
        except TradeExecutionBusy:
            return jsonify({"code": 0, "msg": "quickTrade.workflow.executionBusy"}), 409

    return wrapped


serialized_quick_trade_close = serialized_quick_trade_order


def normalized_submission_quantity(
    client: Any,
    *,
    exchange_id: str,
    symbol: str,
    market_type: str,
    requested: float,
    exchange_config: Dict[str, Any],
    source: str,
    snapshotter: Callable[..., tuple[float, Dict[str, Any]]] = exchange_quantity_snapshot,
) -> tuple[float, Dict[str, Any]]:
    executable, snapshot = snapshotter(
        client,
        exchange_id=exchange_id,
        symbol=symbol,
        market_type=market_type,
        requested=requested,
        exchange_config=exchange_config,
        source=source,
    )
    return executable if executable > 0 else requested, snapshot


def spot_dust_metadata(
    *,
    rules: Any,
    size: float,
    available: float,
    mark_price: float,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "available": available,
        "tradable_size": available,
        "is_dust": False,
        "is_locked": size > 0 and available <= 1e-12,
        "locked_size": max(0.0, size - available),
    }
    if rules is None:
        return metadata
    tradable = max(0.0, float(rules.normalize_amount(available, enforce_minimum=True)))
    min_amount = max(0.0, float(rules.min_amount or 0.0))
    min_notional = max(0.0, float(rules.min_notional or 0.0))
    below_notional = mark_price > 0 and min_notional > 0 and tradable * mark_price + 1e-12 < min_notional
    metadata.update(
        tradable_size=tradable,
        minimum_trade_quantity=min_amount,
        minimum_trade_notional=min_notional,
        is_dust=available > 0 and (tradable <= 0 or below_notional),
    )
    if metadata["is_dust"]:
        metadata["dust_reason"] = "minimum_notional" if below_notional else "minimum_quantity"
    return metadata


def matching_position_size(
    positions: List[Dict[str, Any]],
    *,
    symbol: str,
    position_side: str,
) -> float:
    target_side = str(position_side or "").strip().lower()
    total = 0.0
    for position in positions or []:
        if not symbols_match(symbol, str(position.get("symbol") or "")):
            continue
        if str(position.get("side") or "").strip().lower() != target_side:
            continue
        try:
            total += max(0.0, float(position.get("size") or 0.0))
        except (TypeError, ValueError):
            continue
    return total


def verify_full_market_close(
    *,
    client: Any,
    exchange_config: Dict[str, Any],
    symbol: str,
    market_type: str,
    position_side: str,
    requested: float,
    filled: float,
    status: str,
    fetch_positions: Callable[..., Dict[str, Any]],
    parse_positions: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
) -> Dict[str, Any]:
    positions = parse_positions(
        fetch_positions(
            client,
            exchange_config,
            symbol=symbol,
            market_type=market_type,
        )
    )
    remaining = matching_position_size(
        positions,
        symbol=symbol,
        position_side=position_side,
    )
    tolerance = max(1e-12, requested * 1e-9)
    complete = remaining <= tolerance
    terminal = status in {"filled", "cancelled"}
    retry = min(remaining, requested) if not complete and terminal else 0.0
    return {
        "remaining_size": remaining,
        "close_complete": complete,
        "retry_close_size": retry,
        "status": "partially_filled" if retry > 0 else status,
    }
