"""Late order-state reconciliation for Quick Trade history."""

from __future__ import annotations

import json
from typing import Any, Dict

from app.services.quick_trade.credentials import build_exchange_config, create_exchange_client
from app.services.quick_trade.history import parse_quick_trade_metadata
from app.services.quick_trade.orders import enrich_fill, quick_order_status
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.request_guard import cache_key, guarded_cached


logger = get_logger(__name__)

_ACTIVE_STATUSES = {
    "new",
    "open",
    "pending",
    "accepted",
    "submitted",
    "partial",
    "partially_filled",
}
_RECONCILABLE_EXCHANGES = frozenset(
    {"binance", "bitget", "bybit", "gate", "gateio", "htx", "okx"}
)


def _exchange_order_updates(
    client: Any,
    symbol: str,
    market_type: str,
    rows: list[Dict[str, Any]],
) -> Dict[int, Dict[str, Any]]:
    updates: Dict[int, Dict[str, Any]] = {}
    for row in rows[:8]:
        order_id = str(row.get("exchange_order_id") or "").strip()
        if not order_id:
            continue
        metadata = parse_quick_trade_metadata(row.get("raw_result"))
        snapshot = enrich_fill(
            client,
            order_id=order_id,
            symbol=symbol,
            market_type=market_type,
            max_wait_sec=0,
            include_fees=False,
        )
        filled = max(
            float(row.get("filled_amount") or 0.0),
            float(snapshot.get("filled") or 0.0),
        )
        avg_price = float(snapshot.get("avg_price") or row.get("avg_fill_price") or 0.0)
        exchange_status = str(snapshot.get("status") or "")
        status = quick_order_status(
            requested_qty=float(metadata.get("requested_base_qty") or 0.0),
            filled_qty=filled,
            exchange_status=exchange_status,
        )
        updates[int(row["id"])] = {
            "status": status,
            "filled_amount": filled,
            "avg_fill_price": avg_price,
            "exchange_status": exchange_status,
        }
    return updates


def reconcile_quick_trade_orders(
    rows: list[Dict[str, Any]],
    *,
    user_id: int,
    credential_id: int,
    symbol: str,
    market_type: str,
) -> None:
    """Refresh active rows from the exchange's authoritative order detail."""
    if credential_id <= 0 or not symbol or market_type not in {"spot", "swap"}:
        return
    candidates = [
        row
        for row in rows
        if str(row.get("exchange_id") or "").strip().lower()
        in _RECONCILABLE_EXCHANGES
        and str(row.get("status") or "").strip().lower() in _ACTIVE_STATUSES
        and str(row.get("exchange_order_id") or "").strip()
    ]
    if not candidates:
        return

    try:
        config = build_exchange_config(
            credential_id,
            user_id,
            {"market_type": market_type},
        )
        client = create_exchange_client(config, market_type=market_type)
        order_ids = ",".join(
            str(row.get("exchange_order_id") or "") for row in candidates[:8]
        )
        updates = guarded_cached(
            cache_key(
                "quick_trade_order_reconciliation",
                user_id,
                credential_id,
                market_type,
                symbol,
                order_ids,
            ),
            lambda: _exchange_order_updates(client, symbol, market_type, candidates),
            ttl_sec=1,
            stale_ttl_sec=5,
            timeout_sec=8,
            namespace="quick_trade_order_reconciliation",
            max_concurrent=4,
        )
        if not updates:
            return

        with get_db_connection() as db:
            cur = db.cursor()
            for row in candidates:
                update = updates.get(int(row.get("id") or 0))
                if not update:
                    continue
                stored_raw = row.get("raw_result") or {}
                if isinstance(stored_raw, str):
                    try:
                        stored_raw = json.loads(stored_raw) or {}
                    except (TypeError, ValueError):
                        stored_raw = {}
                if not isinstance(stored_raw, dict):
                    stored_raw = {"raw": stored_raw}
                metadata = stored_raw.get("_quick_trade")
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata["exchange_status"] = update["exchange_status"]
                stored_raw["_quick_trade"] = metadata
                cur.execute(
                    """
                    UPDATE qd_quick_trades
                    SET status = %s,
                        filled_amount = %s,
                        avg_fill_price = %s,
                        raw_result = %s
                    WHERE id = %s AND user_id = %s AND credential_id = %s
                    """,
                    (
                        update["status"],
                        update["filled_amount"],
                        update["avg_fill_price"],
                        json.dumps(stored_raw, ensure_ascii=False),
                        row.get("id"),
                        user_id,
                        credential_id,
                    ),
                )
                row.update(update, raw_result=stored_raw)
            db.commit()
            cur.close()
    except Exception as exc:
        logger.info("Quick Trade order reconciliation deferred: %s", exc)
