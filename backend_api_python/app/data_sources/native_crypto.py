"""Native public REST clients for supported cryptocurrency exchanges."""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


SUPPORTED_EXCHANGES = ("binance", "bitget", "bybit", "okx", "gate", "htx")

_COMMON_TIMEFRAMES = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "1w": "1w",
}

_SUPPORTED_TIMEFRAMES = {
    "binance": set(_COMMON_TIMEFRAMES),
    "okx": {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"},
    "bybit": {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"},
    "bitget": {"1m", "5m", "15m", "30m", "1h", "4h", "6h", "12h", "1d"},
    "gate": {"1m", "5m", "15m", "30m", "1h", "4h", "8h", "1d", "1w"},
    "htx": {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"},
}

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}


class NativeCryptoAPIError(RuntimeError):
    """Raised when an exchange public API returns an unusable response."""


def normalize_exchange_id(exchange_id: str) -> str:
    value = str(exchange_id or "").strip().lower()
    if value == "huobi":
        value = "htx"
    if value not in SUPPORTED_EXCHANGES:
        raise ValueError(f"Unsupported crypto exchange: {value or '<empty>'}")
    return value


def normalize_market_type(market_type: str) -> str:
    value = str(market_type or "spot").strip().lower()
    if value in {"future", "futures", "perp", "perpetual", "linear"}:
        value = "swap"
    if value not in {"spot", "swap"}:
        raise ValueError(f"Unsupported crypto market type: {value}")
    return value


def _symbol_parts(symbol: str) -> Tuple[str, str]:
    value = str(symbol or "").strip().upper().split(":", 1)[0]
    if "/" in value:
        base, quote = value.split("/", 1)
        return base, quote
    for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH"):
        if value.endswith(quote) and len(value) > len(quote):
            return value[: -len(quote)], quote
    return value, "USDT"


def _canonical_symbol(base: str, quote: str, market_type: str) -> str:
    core = f"{str(base).upper()}/{str(quote).upper()}"
    return f"{core}:{str(quote).upper()}" if market_type == "swap" else core


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# Fail-closed Gate swap volume unit error identifiers (DESIGN-06-002 V1.1
# §3.2). These are deterministic unit/metadata failures: consumers must never
# retry them, swap them for another venue's data, or return empty success.
GATE_SWAP_VOLUME_ERROR_CODES = (
    "gate.swapVolumeMetadataMissing",
    "gate.swapVolumeMetadataInvalid",
    "gate.swapVolumeHistoricalUnverified",
    "gate.swapVolumeInvalid",
    "gate.swapVolumeEvidenceMissing",
)


def _is_gate_swap_volume_error(exc: Any) -> bool:
    """True only for the five fail-closed Gate swap volume unit errors."""
    text = str(exc or "")
    return any(code in text for code in GATE_SWAP_VOLUME_ERROR_CODES)


def _ticker(
    *,
    symbol: str,
    last: Any,
    open_price: Any = 0,
    high: Any = 0,
    low: Any = 0,
    change: Any = None,
    percentage: Any = None,
    quote_volume: Any = 0,
    timestamp: Any = None,
) -> Dict[str, Any]:
    last_value = _float(last)
    open_value = _float(open_price)
    change_value = _float(change) if change is not None else last_value - open_value
    percentage_value = (
        _float(percentage)
        if percentage is not None
        else ((change_value / open_value) * 100 if open_value else 0.0)
    )
    return {
        "symbol": symbol,
        "last": last_value,
        "close": last_value,
        "open": open_value,
        "high": _float(high),
        "low": _float(low),
        "change": change_value,
        "percentage": percentage_value,
        "changePercent": percentage_value,
        "quoteVolume": _float(quote_volume),
        "timestamp": int(_float(timestamp)) if timestamp is not None else None,
    }


class NativeCryptoPublicClient:
    """Small exchange-native client with the contract used by CryptoDataSource."""

    def __init__(
        self,
        exchange_id: str,
        market_type: str = "spot",
        *,
        timeout_ms: int = 10000,
        proxy: str = "",
        okx_host: str = "",
    ) -> None:
        self.id = normalize_exchange_id(exchange_id)
        self.market_type = normalize_market_type(market_type)
        self.timeout = max(float(timeout_ms or 10000) / 1000.0, 1.0)
        self.enableRateLimit = True
        self.timeframes = {key: key for key in _SUPPORTED_TIMEFRAMES[self.id]}
        self.markets: Dict[str, Dict[str, Any]] = {}
        self._markets_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0
        self._session_local = threading.local()
        self._proxies = {"http": proxy, "https": proxy} if proxy else None
        self._okx_host = str(okx_host or "www.okx.com").strip()
        # Bounded Gate swap volume-evidence state (DESIGN-06-002 V1.1 §2.2):
        # thread-local last-page store plus the metadata snapshot retrieval
        # timestamp. No other venue keeps state here.
        self._gate_swap_state = threading.local()
        self._gate_swap_metadata_retrieved_at = ""

    def _throttle(self) -> None:
        with self._request_lock:
            remaining = 0.04 - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
            self._last_request_at = time.monotonic()

    def _http_session(self) -> requests.Session:
        session = getattr(self._session_local, "value", None)
        if session is None:
            session = requests.Session()
            self._session_local.value = session
        return session

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self._throttle()
        response = self._http_session().get(
            url,
            params=params or {},
            timeout=self.timeout,
            proxies=self._proxies,
            headers={"User-Agent": "QuantDinger/5 public-market"},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            code = payload.get("code")
            if code not in (None, 0, "0", "00000", 200, "200"):
                raise NativeCryptoAPIError(
                    f"{self.id} public API error {code}: "
                    f"{payload.get('msg') or payload.get('message') or payload}"
                )
            if payload.get("status") not in (None, "ok"):
                raise NativeCryptoAPIError(f"{self.id} public API error: {payload}")
            if payload.get("retCode") not in (None, 0, "0"):
                raise NativeCryptoAPIError(
                    f"{self.id} public API error {payload.get('retCode')}: {payload.get('retMsg')}"
                )
            if payload.get("label") and payload.get("message"):
                raise NativeCryptoAPIError(
                    f"{self.id} public API error {payload.get('label')}: {payload.get('message')}"
                )
        return payload

    def _native_symbol(self, symbol: str) -> str:
        base, quote = _symbol_parts(symbol)
        if self.id in {"binance", "bybit", "bitget"}:
            return f"{base}{quote}"
        if self.id == "okx":
            return f"{base}-{quote}-SWAP" if self.market_type == "swap" else f"{base}-{quote}"
        if self.id == "gate":
            return f"{base}_{quote}"
        if self.id == "htx":
            return f"{base}-{quote}" if self.market_type == "swap" else f"{base}{quote}".lower()
        raise ValueError(f"Unsupported crypto exchange: {self.id}")

    def load_markets(self, reload: bool = False) -> Dict[str, Dict[str, Any]]:
        if self.markets and not reload:
            return self.markets
        with self._markets_lock:
            if self.markets and not reload:
                return self.markets
            rows = self._fetch_market_rows()
            markets: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                base = str(row.get("base") or "").upper()
                quote = str(row.get("quote") or "").upper()
                if not base or not quote:
                    continue
                symbol = _canonical_symbol(base, quote, self.market_type)
                info = {
                    "id": str(row.get("id") or self._native_symbol(symbol)),
                    "symbol": symbol,
                    "base": base,
                    "quote": quote,
                    "settle": str(row.get("settle") or quote).upper(),
                    "active": bool(row.get("active", True)),
                    "spot": self.market_type == "spot",
                    "swap": self.market_type == "swap",
                    "contract": self.market_type == "swap",
                    "linear": self.market_type == "swap" and str(row.get("settle") or quote).upper() == quote,
                    "future": False,
                    "type": self.market_type,
                    "info": row.get("info") or row,
                }
                markets[symbol] = info
                if self.market_type == "swap":
                    markets.setdefault(f"{base}/{quote}", info)
            self.markets = markets
            if self.id == "gate" and self.market_type == "swap":
                self._gate_swap_metadata_retrieved_at = (
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                )
            return markets

    def _fetch_market_rows(self) -> List[Dict[str, Any]]:
        if self.id == "binance":
            host = "https://fapi.binance.com" if self.market_type == "swap" else "https://api.binance.com"
            path = "/fapi/v1/exchangeInfo" if self.market_type == "swap" else "/api/v3/exchangeInfo"
            payload = self._get(host + path)
            rows = []
            for item in payload.get("symbols", []):
                active = item.get("status") == "TRADING"
                if self.market_type == "swap" and item.get("contractType") != "PERPETUAL":
                    continue
                rows.append({
                    "id": item.get("symbol"),
                    "base": item.get("baseAsset"),
                    "quote": item.get("quoteAsset"),
                    "settle": item.get("marginAsset") or item.get("quoteAsset"),
                    "active": active,
                    "info": item,
                })
            return rows
        if self.id == "okx":
            payload = self._get(
                f"https://{self._okx_host}/api/v5/public/instruments",
                {"instType": "SWAP" if self.market_type == "swap" else "SPOT"},
            )
            rows = []
            for item in payload.get("data", []):
                inst_id = str(item.get("instId") or "")
                parts = inst_id.split("-")
                base = item.get("baseCcy") or item.get("ctValCcy") or (parts[0] if parts else "")
                quote = item.get("quoteCcy") or item.get("settleCcy") or (parts[1] if len(parts) > 1 else "")
                rows.append({
                    "id": inst_id,
                    "base": base,
                    "quote": quote,
                    "settle": item.get("settleCcy") or quote,
                    "active": item.get("state") == "live",
                    "info": item,
                })
            return rows
        if self.id == "bybit":
            category = "linear" if self.market_type == "swap" else "spot"
            cursor = ""
            rows = []
            for _ in range(10):
                params: Dict[str, Any] = {"category": category}
                if self.market_type == "swap":
                    params["limit"] = 1000
                if cursor:
                    params["cursor"] = cursor
                payload = self._get("https://api.bybit.com/v5/market/instruments-info", params)
                result = payload.get("result") or {}
                for item in result.get("list") or []:
                    rows.append({
                        "id": item.get("symbol"),
                        "base": item.get("baseCoin"),
                        "quote": item.get("quoteCoin"),
                        "settle": item.get("settleCoin") or item.get("quoteCoin"),
                        "active": item.get("status") == "Trading",
                        "info": item,
                    })
                next_cursor = str(result.get("nextPageCursor") or "")
                if self.market_type == "spot" or not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
            return rows
        if self.id == "bitget":
            payload = self._get(
                "https://api.bitget.com/api/v3/market/instruments",
                {"category": "USDT-FUTURES" if self.market_type == "swap" else "SPOT"},
            )
            rows = []
            for item in payload.get("data") or []:
                status = str(item.get("status") or item.get("symbolStatus") or "").lower()
                rows.append({
                    "id": item.get("symbol"),
                    "base": item.get("baseCoin"),
                    "quote": item.get("quoteCoin"),
                    "settle": item.get("quoteCoin"),
                    "active": status == "online",
                    "info": item,
                })
            return rows
        if self.id == "gate":
            path = "/api/v4/futures/usdt/contracts" if self.market_type == "swap" else "/api/v4/spot/currency_pairs"
            payload = self._get("https://api.gateio.ws" + path)
            rows = []
            for item in payload:
                native_id = str(item.get("name") or item.get("id") or "")
                parts = native_id.split("_")
                rows.append({
                    "id": native_id,
                    "base": item.get("base") or (parts[0] if parts else ""),
                    "quote": item.get("quote") or (parts[1] if len(parts) > 1 else "USDT"),
                    "settle": "USDT",
                    "active": not bool(item.get("in_delisting")) and item.get("trade_status", "tradable") == "tradable",
                    "info": item,
                })
            return rows
        if self.id == "htx":
            if self.market_type == "swap":
                payload = self._get("https://api.hbdm.com/linear-swap-api/v1/swap_contract_info")
                rows = []
                for item in payload.get("data") or []:
                    code = str(item.get("contract_code") or "")
                    parts = code.split("-")
                    rows.append({
                        "id": code,
                        "base": item.get("symbol") or (parts[0] if parts else ""),
                        "quote": parts[1] if len(parts) > 1 else "USDT",
                        "settle": "USDT",
                        "active": int(item.get("contract_status") or 0) == 1,
                        "info": item,
                    })
                return rows
            payload = self._get("https://api.huobi.pro/v1/common/symbols")
            return [
                {
                    "id": item.get("symbol"),
                    "base": item.get("base-currency"),
                    "quote": item.get("quote-currency"),
                    "active": item.get("state") == "online",
                    "info": item,
                }
                for item in payload.get("data") or []
            ]
        raise ValueError(f"Unsupported crypto exchange: {self.id}")

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        native = self._native_symbol(symbol)
        canonical = _canonical_symbol(*_symbol_parts(symbol), self.market_type)
        if self.id == "binance":
            host = "https://fapi.binance.com" if self.market_type == "swap" else "https://api.binance.com"
            path = "/fapi/v1/ticker/24hr" if self.market_type == "swap" else "/api/v3/ticker/24hr"
            row = self._get(host + path, {"symbol": native})
            return _ticker(
                symbol=canonical,
                last=row.get("lastPrice"),
                open_price=row.get("openPrice"),
                high=row.get("highPrice"),
                low=row.get("lowPrice"),
                change=row.get("priceChange"),
                percentage=row.get("priceChangePercent"),
                quote_volume=row.get("quoteVolume"),
                timestamp=row.get("closeTime"),
            )
        if self.id == "okx":
            payload = self._get(f"https://{self._okx_host}/api/v5/market/ticker", {"instId": native})
            rows = payload.get("data") or []
            if not rows:
                raise NativeCryptoAPIError(f"symbol not found: {native}")
            row = rows[0]
            return _ticker(
                symbol=canonical,
                last=row.get("last"),
                open_price=row.get("open24h"),
                high=row.get("high24h"),
                low=row.get("low24h"),
                quote_volume=row.get("volCcy24h"),
                timestamp=row.get("ts"),
            )
        if self.id == "bybit":
            payload = self._get(
                "https://api.bybit.com/v5/market/tickers",
                {"category": "linear" if self.market_type == "swap" else "spot", "symbol": native},
            )
            rows = (payload.get("result") or {}).get("list") or []
            if not rows:
                raise NativeCryptoAPIError(f"symbol not found: {native}")
            row = rows[0]
            pct = _float(row.get("price24hPcnt")) * 100
            return _ticker(
                symbol=canonical,
                last=row.get("lastPrice"),
                open_price=row.get("prevPrice24h"),
                high=row.get("highPrice24h"),
                low=row.get("lowPrice24h"),
                percentage=pct,
                quote_volume=row.get("turnover24h"),
                timestamp=payload.get("time"),
            )
        if self.id == "bitget":
            payload = self._get(
                "https://api.bitget.com/api/v3/market/tickers",
                {
                    "category": "USDT-FUTURES" if self.market_type == "swap" else "SPOT",
                    "symbol": native,
                },
            )
            rows = payload.get("data") or []
            row = rows[0] if isinstance(rows, list) and rows else rows
            if not isinstance(row, dict):
                raise NativeCryptoAPIError(f"symbol not found: {native}")
            change_24h = row.get("price24hPcnt")
            return _ticker(
                symbol=canonical,
                last=row.get("lastPrice"),
                open_price=row.get("openPrice24h"),
                high=row.get("highPrice24h"),
                low=row.get("lowPrice24h"),
                percentage=_float(change_24h) * 100 if change_24h is not None else None,
                quote_volume=row.get("turnover24h"),
                timestamp=row.get("ts") or payload.get("requestTime"),
            )
        if self.id == "gate":
            if self.market_type == "swap":
                rows = self._get(
                    "https://api.gateio.ws/api/v4/futures/usdt/tickers",
                    {"contract": native},
                )
            else:
                rows = self._get(
                    "https://api.gateio.ws/api/v4/spot/tickers",
                    {"currency_pair": native},
                )
            if not rows:
                raise NativeCryptoAPIError(f"symbol not found: {native}")
            row = rows[0]
            return _ticker(
                symbol=canonical,
                last=row.get("last"),
                open_price=row.get("open"),
                high=row.get("high_24h") or row.get("high_24h_usd"),
                low=row.get("low_24h") or row.get("low_24h_usd"),
                percentage=row.get("change_percentage"),
                quote_volume=row.get("quote_volume") or row.get("volume_24h_quote"),
            )
        if self.id == "htx":
            if self.market_type == "swap":
                payload = self._get(
                    "https://api.hbdm.com/linear-swap-ex/market/detail/merged",
                    {"contract_code": native},
                )
            else:
                payload = self._get(
                    "https://api.huobi.pro/market/detail/merged",
                    {"symbol": native},
                )
            row = payload.get("tick") or {}
            if not row:
                raise NativeCryptoAPIError(f"symbol not found: {native}")
            return _ticker(
                symbol=canonical,
                last=row.get("close"),
                open_price=row.get("open"),
                high=row.get("high"),
                low=row.get("low"),
                quote_volume=row.get("vol"),
                timestamp=payload.get("ts"),
            )
        raise ValueError(f"Unsupported crypto exchange: {self.id}")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int] = None,
        limit: int = 300,
    ) -> List[List[Any]]:
        if self.id == "gate" and self.market_type == "swap":
            self._gate_swap_state.identity = None
            self._gate_swap_state.bars = {}
        if timeframe not in self.timeframes:
            raise NativeCryptoAPIError(f"Unsupported timeframe {timeframe} on {self.id}")
        limit = max(1, min(int(limit or 300), 1000))
        native = self._native_symbol(symbol)
        tf_ms = int(_TIMEFRAME_SECONDS[timeframe] * 1000)
        rows: Iterable[Any]
        if self.id == "binance":
            host = "https://fapi.binance.com" if self.market_type == "swap" else "https://api.binance.com"
            path = "/fapi/v1/klines" if self.market_type == "swap" else "/api/v3/klines"
            params: Dict[str, Any] = {"symbol": native, "interval": timeframe, "limit": limit}
            if since is not None:
                params["startTime"] = int(since)
            rows = self._get(host + path, params)
            parsed = [[int(row[0]), _float(row[1]), _float(row[2]), _float(row[3]), _float(row[4]), _float(row[5])] for row in rows]
        elif self.id == "okx":
            bar = {"1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H", "1d": "1D", "1w": "1W"}.get(timeframe, timeframe)
            params = {"instId": native, "bar": bar, "limit": min(limit, 300)}
            path = "/api/v5/market/candles"
            if since is not None:
                path = "/api/v5/market/history-candles"
                params["after"] = int(since) + tf_ms * min(limit, 300)
            payload = self._get(f"https://{self._okx_host}{path}", params)
            rows = payload.get("data") or []
            parsed = [[int(row[0]), _float(row[1]), _float(row[2]), _float(row[3]), _float(row[4]), _float(row[5])] for row in rows]
        elif self.id == "bybit":
            interval = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720", "1d": "D", "1w": "W"}[timeframe]
            params = {
                "category": "linear" if self.market_type == "swap" else "spot",
                "symbol": native,
                "interval": interval,
                "limit": min(limit, 1000),
            }
            if since is not None:
                params["start"] = int(since)
                params["end"] = int(since) + tf_ms * min(limit, 1000)
            payload = self._get("https://api.bybit.com/v5/market/kline", params)
            rows = (payload.get("result") or {}).get("list") or []
            parsed = [[int(row[0]), _float(row[1]), _float(row[2]), _float(row[3]), _float(row[4]), _float(row[5])] for row in rows]
        elif self.id == "bitget":
            granularity = {
                "1m": "1m",
                "3m": "3m",
                "5m": "5m",
                "15m": "15m",
                "30m": "30m",
                "1h": "1H",
                "4h": "4H",
                "6h": "6H",
                "12h": "12H",
                "1d": "1D",
                "1w": "1W",
            }[timeframe]
            params = {
                "category": "USDT-FUTURES" if self.market_type == "swap" else "SPOT",
                "symbol": native,
                "interval": granularity,
                "limit": min(limit, 1000),
            }
            if since is not None:
                params["startTime"] = int(since)
                max_window_ms = 90 * 24 * 60 * 60 * 1000
                params["endTime"] = min(
                    int(since) + tf_ms * min(limit, 1000),
                    int(since) + max_window_ms,
                    int(time.time() * 1000),
                )
            payload = self._get("https://api.bitget.com/api/v3/market/candles", params)
            rows = payload.get("data") or []
            parsed = [[int(row[0]), _float(row[1]), _float(row[2]), _float(row[3]), _float(row[4]), _float(row[5])] for row in rows]
        elif self.id == "gate":
            interval = "7d" if timeframe == "1w" else timeframe
            params = {"interval": interval, "limit": min(limit, 1000)}
            if since is not None:
                params["from"] = int(since // 1000)
                params["to"] = int((int(since) + tf_ms * min(limit, 1000)) // 1000)
                params.pop("limit", None)
            if self.market_type == "swap":
                params["contract"] = native
                rows = self._get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks", params)
                parsed = self._parse_gate_swap_ohlcv(
                    rows,
                    symbol=symbol,
                    timeframe=timeframe,
                    since_ms=int(since) if since is not None else None,
                )
            else:
                params["currency_pair"] = native
                rows = self._get("https://api.gateio.ws/api/v4/spot/candlesticks", params)
                parsed = [[int(row[0]) * 1000, _float(row[5]), _float(row[3]), _float(row[4]), _float(row[2]), _float(row[6])] for row in rows]
        elif self.id == "htx":
            period = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "4h": "4hour", "1d": "1day", "1w": "1week"}[timeframe]
            params = {"period": period, "size": min(limit, 2000)}
            if self.market_type == "swap":
                params["contract_code"] = native
                url = "https://api.hbdm.com/linear-swap-ex/market/history/kline"
            else:
                params["symbol"] = native
                url = "https://api.huobi.pro/market/history/kline"
            payload = self._get(url, params)
            rows = payload.get("data") or []
            parsed = [[int(row.get("id") or 0) * 1000, _float(row.get("open")), _float(row.get("high")), _float(row.get("low")), _float(row.get("close")), _float(row.get("amount"))] for row in rows]
        else:
            raise ValueError(f"Unsupported crypto exchange: {self.id}")
        lower_bound = int(since) if since is not None else 0
        unique = {int(row[0]): row for row in parsed if row and int(row[0]) >= lower_bound}
        returned = sorted(unique.values(), key=lambda row: row[0])[:limit]
        if self.id == "gate" and self.market_type == "swap":
            state = self._gate_swap_state
            final_stamps = {int(row[0]) for row in returned}
            state.bars = {
                stamp: item for stamp, item in state.bars.items() if stamp in final_stamps
            }
        return returned

    def _gate_swap_contract_metadata(self, native_contract: str) -> Dict[str, Any]:
        """Return the Gate USDT-settled direct linear contract record, or raise.

        Uses the ``load_markets()`` snapshot only (``markets[symbol].info``);
        no per-bar metadata requests, no trading client, no credentials.
        """
        try:
            self.load_markets()
        except NativeCryptoAPIError:
            raise
        except Exception as exc:  # pragma: no cover - defensive, offline tested via mocks
            raise NativeCryptoAPIError(
                f"gate.swapVolumeMetadataMissing: cannot load contract metadata for "
                f"{native_contract}: {exc}"
            ) from exc
        target = str(native_contract or "").strip().upper()
        for market in (self.markets or {}).values():
            if str(market.get("id") or "").upper() != target:
                continue
            settle = str(market.get("settle") or "").upper()
            quote = str(market.get("quote") or "").upper()
            info = market.get("info") or {}
            contract_type = str(info.get("type") or "").lower()
            if settle != "USDT" or contract_type != "direct":
                raise NativeCryptoAPIError(
                    f"gate.swapVolumeMetadataInvalid: contract {native_contract} is not a "
                    f"USDT-settled direct linear contract (settle={settle or '<missing>'}, "
                    f"type={contract_type or '<missing>'})"
                )
            return {"id": market.get("id"), "settle": settle, "info": info}
        raise NativeCryptoAPIError(
            f"gate.swapVolumeMetadataMissing: contract {native_contract} not found in the "
            f"current Gate USDT futures market snapshot"
        )

    def _parse_gate_swap_ohlcv(
        self,
        rows: Iterable[Any],
        *,
        symbol: str,
        timeframe: str,
        since_ms: Optional[int],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[List[Any]]:
        """Parse Gate swap candlesticks into base-currency six-column OHLCV.

        The private metadata argument is only a synthetic offline parsing seam;
        fetch_ohlcv always uses the current load_markets snapshot, not history.

        base = raw contracts * quanto_multiplier (applied once); quote volume is
        the official ``sum`` only. Any metadata, unit or historical-applicability
        failure raises a fail-closed ``gate.swapVolume*`` NativeCryptoAPIError;
        raw contract counts are never silently forwarded as base volume.
        """
        state = self._gate_swap_state
        state.identity = None
        state.bars = {}
        parsed: List[List[Any]] = []
        identity = {
            "symbol": symbol,
            "interval": timeframe,
            "since_ms": since_ms,
            "native_contract": self._native_symbol(symbol),
        }
        page_evidence: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            bar_open_utc = int(row.get("t") or 0) * 1000
            raw_quote_sum = row.get("sum")
            if metadata is None:
                metadata = self._gate_swap_contract_metadata(identity["native_contract"])
            info = metadata["info"]
            if (
                metadata.get("id") != identity["native_contract"]
                or metadata.get("settle") != "USDT"
                or info.get("name") != identity["native_contract"]
                or info.get("type") != "direct"
            ):
                raise NativeCryptoAPIError(
                    f"gate.swapVolumeMetadataInvalid: contract identity/type mismatch for "
                    f"{identity['native_contract']}"
                )
            raw_multiplier = info.get("quanto_multiplier")
            if raw_multiplier is None:
                raise NativeCryptoAPIError(
                    f"gate.swapVolumeMetadataMissing: quanto_multiplier missing for "
                    f"{identity['native_contract']}"
                )
            try:
                multiplier = float(raw_multiplier)
            except (TypeError, ValueError, OverflowError) as exc:
                raise NativeCryptoAPIError(
                    f"gate.swapVolumeMetadataInvalid: quanto_multiplier for "
                    f"{identity['native_contract']} is not numeric"
                ) from exc
            if isinstance(raw_multiplier, bool) or not math.isfinite(multiplier) or multiplier <= 0:
                raise NativeCryptoAPIError(
                    f"gate.swapVolumeMetadataInvalid: quanto_multiplier for "
                    f"{identity['native_contract']} is not a finite positive number"
                )
            raw_v = row.get("v")
            raw_v_contracts = self._gate_swap_volume_zero_ok(raw_v, "raw_v_contracts")
            if raw_v_contracts == 0.0:
                # A zero contract count is a real zero-trade bar, not dirty data:
                # the conversion is exactly 0 and never reaches the product
                # check (0 x finite positive multiplier is exactly +0.0).
                base_volume = 0.0
            else:
                base_volume = self._gate_swap_volume_number(
                    raw_v_contracts * multiplier, "base_volume"
                )
            quote_volume = (
                None if raw_quote_sum is None
                else self._gate_swap_volume_number(raw_quote_sum, "sum")
            )
            synthetic_metadata = metadata.get("synthetic") is True
            effective_from = metadata.get("effective_from")
            effective_to = metadata.get("effective_to")
            if synthetic_metadata:
                # Offline fixture seam: the synthetic interval must cover the
                # whole bar before its applicability may be called verified.
                try:
                    start = datetime.fromisoformat(str(effective_from).replace("Z", "+00:00"))
                    end = datetime.fromisoformat(str(effective_to).replace("Z", "+00:00"))
                    verified = (
                        metadata.get("metadata_source") == "synthetic"
                        and start.tzinfo is not None and end.tzinfo is not None
                        and start.timestamp() * 1000 <= bar_open_utc
                        and (bar_open_utc + _TIMEFRAME_SECONDS[timeframe] * 1000)
                        <= end.timestamp() * 1000
                    )
                except (TypeError, ValueError, OverflowError):
                    verified = False
                if not verified:
                    raise NativeCryptoAPIError(
                        f"gate.swapVolumeHistoricalUnverified: no applicable metadata for "
                        f"{identity['native_contract']} {timeframe} at bar {bar_open_utc}"
                    )
                applicability = "verified"
                metadata_source = metadata["metadata_source"]
                metadata_retrieved_at = metadata.get("metadata_retrieved_at_utc")
            else:
                # Current contract snapshot: the live multiplier converts the
                # units correctly, but a retrieval-time snapshot proves nothing
                # about historical applicability, so it stays honestly unknown.
                applicability = "unknown"
                metadata_source = "load_markets:/api/v4/futures/usdt/contracts"
                metadata_retrieved_at = self._gate_swap_metadata_retrieved_at or None
                effective_from = None
                effective_to = None
            parsed.append([
                bar_open_utc,
                _float(row.get("o")),
                _float(row.get("h")),
                _float(row.get("l")),
                _float(row.get("c")),
                base_volume,
            ])
            page_evidence[bar_open_utc] = {
                "exchange_id": "gate",
                "market_type": "swap",
                "settle": metadata["settle"],
                "native_contract": identity["native_contract"],
                "bar_open_utc": datetime.fromtimestamp(
                    bar_open_utc / 1000.0, tz=timezone.utc
                ).isoformat().replace("+00:00", "Z"),
                "interval": timeframe,
                "raw_v_contracts": raw_v,
                "raw_quote_sum": raw_quote_sum,
                "quanto_multiplier": raw_multiplier,
                "base_volume": base_volume,
                "quote_volume": quote_volume,
                "volume_unit": "base",
                "source_endpoint": "https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
                "retrieved_at_utc": datetime.now(timezone.utc)
                    .isoformat().replace("+00:00", "Z"),
                "metadata_source": metadata_source,
                "metadata_retrieved_at_utc": metadata_retrieved_at,
                "effective_from": effective_from,
                "effective_to": effective_to,
                "historical_applicability": applicability,
                "normalization_version": "gate_swap_base_v1",
            }
        state.identity = identity
        state.bars = page_evidence
        state.metadata_markets = self.markets
        return parsed

    @staticmethod
    def _gate_swap_volume_zero_ok(value: Any, field: str) -> float:
        """Accept a zero contract count as a legitimate zero-trade bar.

        Only the raw contract count may be zero: null, non-numeric, non-finite
        and negative values keep the fail-closed ``gate.swapVolumeInvalid``
        behaviour of :meth:`_gate_swap_volume_number`. Negative zero is not a
        special case: ``-0.0 == 0`` and ``-0.0 < 0`` is false, so it lands in
        the zero-trade branch by numeric equality.
        """
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise NativeCryptoAPIError(
                f"gate.swapVolumeInvalid: {field} is not numeric"
            ) from exc
        if isinstance(value, bool) or not math.isfinite(number) or number < 0:
            raise NativeCryptoAPIError(
                f"gate.swapVolumeInvalid: {field} is not a finite valid volume"
            )
        if number == 0:
            return 0.0
        return number

    @staticmethod
    def _gate_swap_volume_number(value: Any, field: str) -> float:
        """Reject invalid Gate volume instead of the global permissive fallback."""
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise NativeCryptoAPIError(
                f"gate.swapVolumeInvalid: {field} is not numeric"
            ) from exc
        if (
            isinstance(value, bool) or not math.isfinite(number)
            or number < 0 or (field != "sum" and number == 0)
        ):
            raise NativeCryptoAPIError(
                f"gate.swapVolumeInvalid: {field} is not a finite valid volume"
            )
        return number

    def get_gate_swap_volume_evidence(
        self,
        symbol: str,
        timeframe: str,
        timestamp_ms: int,
    ) -> Optional[Dict[str, Any]]:
        """Read-only last-page volume evidence for one bar (DESIGN-06-002 §3.2).

        Returns None unless the identity (symbol, interval, request window) of
        the most recent fetch on this thread matches exactly — evidence is never
        borrowed across symbols, timeframes or pagination windows.
        """
        state = getattr(self, "_gate_swap_state", None)
        if state is None:
            return None
        identity = getattr(state, "identity", None)
        bars = getattr(state, "bars", None)
        if (
            not identity or not bars
            or getattr(state, "metadata_markets", None) is not self.markets
        ):
            return None
        native_contract = self._native_symbol(symbol)
        since_ms = identity.get("since_ms")
        matches = (
            identity.get("symbol") == symbol
            and identity.get("interval") == timeframe
            and identity.get("native_contract") == native_contract
            and (since_ms is None or timestamp_ms >= int(since_ms))
        )
        if not matches:
            return None
        evidence = bars.get(int(timestamp_ms))
        return dict(evidence) if evidence is not None else None

    def _get_training_payload(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Single bounded public GET for the training-OHLC path only.

        Reuses the existing throttled session, timeout and proxy configuration,
        but never follows redirects and never retries: the training budget
        counts every provider attempt, so silent retries would make it
        unconstrainable. Non-2xx or unusable JSON raises NativeCryptoAPIError.
        """
        self._throttle()
        try:
            response = self._http_session().get(
                url,
                params=params or {},
                timeout=self.timeout,
                proxies=self._proxies,
                headers={"User-Agent": "QuantDinger/5 public-market"},
                allow_redirects=False,
            )
            # raise_for_status only covers 4xx/5xx; the frozen contract rejects
            # every non-2xx outcome (e.g. a 302 carrying valid JSON).
            if not 200 <= response.status_code < 300:
                raise NativeCryptoAPIError(
                    f"training.sourceUnavailable: unexpected HTTP status "
                    f"{response.status_code}"
                )
            return response.json()
        except NativeCryptoAPIError:
            raise
        except Exception as exc:
            raise NativeCryptoAPIError(f"training.sourceUnavailable: {exc}") from exc

    def fetch_training_ohlc(self, symbol: str, *, before_time: int, limit: int = 365) -> Dict[str, Any]:
        """Fetch raw Gate USDT-direct swap daily training candles (DESIGN-06-002 V1.4 §11.2).

        Returns the untouched provider rows plus the whitelisted raw metadata of
        the exact contract. Price-only: no volume parsing, no synthetic
        metadata, no OHLCV service chain, no cache. At most two public GETs
        (one contracts metadata, one bounded daily candlesticks request); no
        pagination, no retry, no redirect. Only gate/swap instances are
        accepted, and no caller-supplied metadata/synthetic/provenance/
        endpoint injection exists by signature.
        """
        if self.id != "gate" or self.market_type != "swap":
            raise NativeCryptoAPIError(
                "training.identityUnverified: training OHLC is restricted to gate swap"
            )
        text = str(symbol or "").strip().upper()
        settle = ""
        if "@" in text or not text:
            raise NativeCryptoAPIError(
                "training.identityUnverified: venue-alias symbols are not accepted"
            )
        if ":" in text:
            text, settle = text.rsplit(":", 1)
        if "/" in text:
            base, quote = text.split("/", 1)
        elif text.endswith("USDT") and len(text) > 4:
            base, quote = text[:-4], "USDT"
        else:
            raise NativeCryptoAPIError(
                "training.identityUnverified: symbol is not a USDT-quoted pair"
            )
        if not base or quote != "USDT" or (settle and settle != "USDT"):
            raise NativeCryptoAPIError(
                "training.identityUnverified: symbol must be a USDT pair/settlement"
            )
        native_contract = f"{base}_USDT"
        if self._native_symbol(f"{base}/USDT:USDT") != native_contract:
            raise NativeCryptoAPIError(
                "training.identityUnverified: canonical round-trip mismatch"
            )
        cutoff = int(before_time)
        if cutoff <= 0 or cutoff % 86400:
            raise NativeCryptoAPIError(
                "training.sourceUnavailable: before_time must be a positive UTC midnight"
            )
        rows_limit = int(limit)
        if not 2 <= rows_limit <= 365:
            raise NativeCryptoAPIError(
                "training.sourceUnavailable: limit must be within 2..365"
            )
        host = "https://api.gateio.ws"
        metadata_path = "/api/v4/futures/usdt/contracts"
        candle_path = "/api/v4/futures/usdt/candlesticks"
        contracts = self._get_training_payload(host + metadata_path)
        if not isinstance(contracts, list):
            raise NativeCryptoAPIError(
                "training.identityUnverified: contracts response is not a list"
            )
        matches = [row for row in contracts
                   if isinstance(row, dict) and row.get("name") == native_contract]
        if len(matches) != 1:
            raise NativeCryptoAPIError(
                f"training.identityUnverified: expected exactly one contract "
                f"{native_contract}, found {len(matches)}"
            )
        row = matches[0]
        if "type" not in row:
            raise NativeCryptoAPIError(
                "training.identityUnverified: contract type missing"
            )
        metadata_raw: Dict[str, Any] = {"name": row["name"], "type": row["type"]}
        if row.get("base") is not None:
            metadata_raw["base"] = row["base"]
        if row.get("quote") is not None:
            metadata_raw["quote"] = row["quote"]
        if row.get("settle") is not None:
            metadata_raw["settle"] = row["settle"]
        metadata_retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        params: Dict[str, Any] = {
            "contract": native_contract,
            "interval": "1d",
            "from": cutoff - rows_limit * 86400,
            "to": cutoff - 1,
        }
        raw_rows = self._get_training_payload(host + candle_path, params)
        if not isinstance(raw_rows, list):
            raise NativeCryptoAPIError(
                "training.sourceUnavailable: candlesticks response is not a list"
            )
        return {
            "raw_rows": raw_rows,
            "metadata_raw": metadata_raw,
            "metadata_retrieved_at_utc": metadata_retrieved_at,
            "request": {
                "host": host,
                "metadata_path": metadata_path,
                "path": candle_path,
                "contract": native_contract,
                "interval": "1d",
                "from": params["from"],
                "to": params["to"],
                "limit": rows_limit,
                "before_time": cutoff,
            },
            "provider_request_count": 2,
            "exchange_id": self.id,
            "market_type": self.market_type,
        }


def create_native_crypto_client(
    exchange_id: str,
    market_type: str = "spot",
    *,
    timeout_ms: int = 10000,
    proxy: str = "",
    okx_host: str = "",
) -> NativeCryptoPublicClient:
    return NativeCryptoPublicClient(
        exchange_id,
        market_type,
        timeout_ms=timeout_ms,
        proxy=proxy,
        okx_host=okx_host,
    )


__all__ = [
    "NativeCryptoAPIError",
    "NativeCryptoPublicClient",
    "SUPPORTED_EXCHANGES",
    "create_native_crypto_client",
    "normalize_exchange_id",
    "normalize_market_type",
]
