"""Cached derivative configuration and order-transport warmup for Quick Trade."""

from __future__ import annotations

from typing import Any, Dict

from app.services.live_trading.account_configuration import configure_derivatives_account
from app.services.live_trading.ws_order_transport import warm_websocket_order_client
from app.utils.logger import get_logger
from app.utils.request_guard import cache_key, guarded_cached


logger = get_logger(__name__)


def configure_quick_trade_derivatives(
    client: Any,
    *,
    user_id: int,
    credential_id: int,
    exchange_id: str,
    symbol: str,
    leverage: float,
    margin_mode: str,
) -> Dict[str, Any]:
    """Apply account settings once per requested configuration for a short TTL."""
    return guarded_cached(
        cache_key(
            "quick_trade_derivatives_config",
            user_id,
            credential_id,
            exchange_id,
            symbol.upper(),
            int(float(leverage or 1)),
            margin_mode,
        ),
        lambda: configure_derivatives_account(
            client,
            exchange_id=exchange_id,
            symbol=symbol,
            leverage=leverage,
            margin_mode=margin_mode,
        ),
        ttl_sec=60,
        stale_ttl_sec=60,
        timeout_sec=15.0,
        namespace="quick_trade_derivatives_config",
        max_concurrent=8,
        cache_if=lambda value: isinstance(value, dict),
    )


def warm_quick_trade_account(
    client: Any,
    *,
    user_id: int,
    credential_id: int,
    exchange_id: str,
    symbol: str,
    market_type: str,
    leverage: float,
    margin_mode: str,
) -> None:
    """Prepare account settings and persistent order transport off the order path."""
    if market_type == "swap" and symbol:
        try:
            configure_quick_trade_derivatives(
                client,
                user_id=user_id,
                credential_id=credential_id,
                exchange_id=exchange_id,
                symbol=symbol,
                leverage=leverage,
                margin_mode=margin_mode,
            )
        except Exception as exc:
            logger.info("Quick Trade account warmup skipped for %s: %s", exchange_id, exc)

    prepare_ws_order_entry = getattr(client, "prepare_ws_order_entry", None)
    if callable(prepare_ws_order_entry) and symbol:
        prepare_ws_order_entry(symbol=symbol, market_type=market_type)
    warm_websocket_order_client(
        client,
        exchange_id=exchange_id,
        market_type=market_type,
    )
