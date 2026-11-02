"""Training-OHLC service: strict parsing, verification and envelope projection.

DESIGN-06-002 V1.4 §11.2/§11.3. This service is deliberately outside the
existing OHLCV service chain: no KlineService, no CacheManager, no
CryptoDataSource fallback/pagination, no volume parsing, no synthetic seam.
The only I/O entry is :func:`get_training_ohlc`, which creates a fresh public
Native client per call (at most one metadata GET plus one bounded daily GET;
no retry, no pagination, no redirect). Timestamps come from the injected
server UTC clock in production.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping

from app.config.data_sources import CryptoPublicConfig
from app.data_sources.native_crypto import (
    NativeCryptoAPIError,
    create_native_crypto_client,
)


SCHEMA_VERSION = "grid_training_ohlc_v1"
DATA_MODE = "training_ohlc"
DAY_SECONDS = 86400


class TrainingRequestError(ValueError):
    """Strict request-parse failure -> HTTP 400 training.invalidRequest."""


class TrainingSourceError(RuntimeError):
    """Provider network/structure failure -> HTTP 502 training.sourceUnavailable."""


class TrainingIdentityError(RuntimeError):
    """Missing/conflicting identity evidence -> HTTP 502 training.identityUnverified."""


def canonical_bytes(value: Any) -> bytes:
    """SHA-256 digest input: sorted keys, compact, UTF-8, no NaN/Inf (SPEC §9.3)."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise TrainingSourceError(
            "training.sourceUnavailable: payload cannot be canonicalized safely"
        ) from exc


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _server_now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _strict_int(raw: Any, field: str) -> int:
    """Parse a positive integer from a string/number, rejecting junk.

    Conversion and datetime-range failures normalize to TrainingRequestError
    (HTTP 400) so no raw ValueError/OSError escapes the route's parse branch.
    """
    if isinstance(raw, bool) or raw is None or raw == "":
        raise TrainingRequestError(f"{field} must be a positive integer")
    if isinstance(raw, str):
        text = raw.strip()
        if not text.isdigit():
            raise TrainingRequestError(f"{field} must be a positive integer")
        try:
            value = int(text)
        except ValueError as exc:
            raise TrainingRequestError(f"{field} must be a positive integer") from exc
    elif isinstance(raw, int):
        value = raw
    else:
        raise TrainingRequestError(f"{field} must be a positive integer")
    if value <= 0:
        raise TrainingRequestError(f"{field} must be a positive integer")
    return value


def parse_training_request(args: Mapping[str, Any], *, now_utc: datetime) -> dict:
    """Strictly parse query args into a validated training request (SPEC §9.2).

    Pure validation: no client/instance is created, no provider call happens
    here, so authorization or parameter failures cost zero provider calls.
    """
    values: dict[str, list[str]] = {}
    getlist = getattr(args, "getlist", None)
    for key in args:
        value = getlist(key) if getlist is not None else args[key]
        if isinstance(value, (list, tuple)):
            items = [str(item) for item in value]
        else:
            items = [str(value)]
        if len(items) > 1:
            raise TrainingRequestError(f"{key} must appear at most once")
        values[key] = items

    def single(name: str) -> str | None:
        items = values.get(name) or []
        if len(items) > 1:
            raise TrainingRequestError(f"{name} must appear at most once")
        return items[0] if items else None

    data_mode = single("data_mode") or "ohlcv"
    if data_mode not in ("ohlcv", "training_ohlc"):
        raise TrainingRequestError("data_mode must be ohlcv or training_ohlc")
    if data_mode != "training_ohlc":
        # The new identity parameters are not generalized to the OHLCV mode.
        if single("exchange_id") or single("market_type"):
            raise TrainingRequestError(
                "exchange_id/market_type are training_ohlc-only parameters")
        return {"data_mode": "ohlcv"}

    exchange_id = single("exchange_id")
    market_type = single("market_type")
    if not exchange_id or not market_type:
        raise TrainingRequestError("exchange_id and market_type are required for training_ohlc")
    if exchange_id.strip().lower() != "gate" or market_type.strip().lower() != "swap":
        raise TrainingRequestError("training_ohlc requires exchange_id=gate and market_type=swap")

    market = (single("market") or "").strip()
    symbol = (single("symbol") or "").strip()
    if not market or not symbol:
        raise TrainingRequestError("market and symbol are required")
    if market != "Crypto":
        raise TrainingRequestError("training_ohlc requires market=Crypto")
    # Reject conflicting caller aliases as input errors before creating a client.
    pair, separator, settle = symbol.upper().partition(":")
    if "@" in symbol or (separator and settle != "USDT"):
        raise TrainingRequestError("symbol venue/settlement conflicts with gate swap")
    if "/" in pair:
        base, quote = pair.split("/", 1)
    elif pair.endswith("USDT"):
        base, quote = pair[:-4], "USDT"
    else:
        raise TrainingRequestError("symbol must be a USDT pair")
    if not base or quote != "USDT":
        raise TrainingRequestError("symbol must be a USDT pair")

    timeframe = single("timeframe")
    if timeframe is None:
        # Only a truly absent parameter falls back to the default; an explicit
        # empty value stays an input error (Design §11.3: no empty clamping).
        timeframe = "1D"
    else:
        timeframe = timeframe.strip()
        if not timeframe:
            raise TrainingRequestError("timeframe must not be empty")
    if timeframe != "1D":
        raise TrainingRequestError("training_ohlc requires timeframe=1D")

    before_raw = single("before_time")
    before_alias = single("beforeTime")
    if before_raw is not None and before_alias is not None:
        raise TrainingRequestError("before_time and beforeTime are mutually exclusive")
    before_value = _strict_int(before_raw or before_alias, "before_time")
    try:
        cutoff = datetime.fromtimestamp(before_value, tz=timezone.utc)
    except (OSError, OverflowError, ValueError) as exc:
        raise TrainingRequestError("before_time must be a valid unix timestamp") from exc
    if cutoff > now_utc:
        raise TrainingRequestError("before_time must not be in the future")
    if before_value % DAY_SECONDS:
        raise TrainingRequestError("before_time must be a UTC midnight")

    limit_raw = single("limit")
    if limit_raw is None:
        limit = 365
    else:
        limit = _strict_int(limit_raw, "limit")
        if not 2 <= limit <= 365:
            raise TrainingRequestError("limit must be within 2..365")

    return {
        "data_mode": "training_ohlc",
        "market": market,
        "symbol": symbol,
        "timeframe": timeframe,
        "exchange_id": "gate",
        "market_type": "swap",
        "before_time": before_value,
        "limit": limit,
    }


def _price(raw: Any, field: str) -> float:
    if isinstance(raw, bool) or raw is None:
        raise TrainingRequestError(f"{field} is not a usable price")
    if isinstance(raw, str) and raw.strip().lower() in ("nan", "inf", "-inf", "+inf"):
        # A string 'NaN' stays safely representable in raw_rows but the price
        # itself is illegal -> insufficient_evidence, not a structural error.
        raise TrainingRequestError(f"{field} is not a usable price")
    if isinstance(raw, str):
        try:
            number = float(raw)
        except ValueError as exc:
            raise TrainingRequestError(f"{field} is not a usable price") from exc
    elif isinstance(raw, (int, float)):
        number = float(raw)
    else:
        raise TrainingRequestError(f"{field} is not a usable price")
    if not math.isfinite(number) or number <= 0:
        raise TrainingRequestError(f"{field} is not a usable price")
    return number


def _bar_time(raw: Any) -> int:
    """t is Unix seconds; a non-integer upstream value is a provider structure
    failure (TrainingSourceError -> 502), not a caller request error."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TrainingSourceError("training.sourceUnavailable: bar t must be integer unix seconds")
    return raw


def _validate_identity(raw: Mapping[str, Any], native_contract: str) -> str:
    """Cross-check raw contract metadata against the requested mapping (SPEC §9.2)."""
    name = raw.get("name")
    contract_type = raw.get("type")
    if not isinstance(name, str) or not isinstance(contract_type, str):
        raise TrainingIdentityError(
            "training.identityUnverified: contract name/type missing"
        )
    if name != native_contract:
        raise TrainingIdentityError(
            "training.identityUnverified: contract name mismatch"
        )
    base = raw.get("base")
    quote = raw.get("quote")
    settle = raw.get("settle")
    if settle is not None and str(settle).upper() != "USDT":
        raise TrainingIdentityError(
            "training.identityUnverified: settlement is not USDT"
        )
    if quote is not None and str(quote).upper() != "USDT":
        raise TrainingIdentityError(
            "training.identityUnverified: quote is not USDT"
        )
    if base is not None:
        expected_base = native_contract.rsplit("_USDT", 1)[0]
        if str(base).upper() != expected_base:
            raise TrainingIdentityError(
                "training.identityUnverified: base mismatch"
            )
    if str(contract_type).lower() != "direct":
        raise TrainingIdentityError(
            "training.identityUnverified: contract is not a direct linear contract"
        )
    return name


def normalize_training_payload(payload: dict, request: dict, *, observed_at_utc: str) -> dict:
    """Pure verification/normalization of one Native training payload (§11.2).

    Keeps raw rows untouched (original order/value/type), validates identity,
    builds ascending normalized OHLC bars with full continuity accounting, and
    computes the three SHA-256 digests. Never invents volume, listing evidence
    or synthetic flags.
    """
    observed = _iso_utc(datetime.fromisoformat(observed_at_utc.replace("Z", "+00:00")))
    raw_rows = payload.get("raw_rows")
    metadata_raw = payload.get("metadata_raw") or {}
    request_meta = payload.get("request") or {}
    if not isinstance(raw_rows, list):
        raise TrainingSourceError("training.sourceUnavailable: raw_rows is not a list")

    provider_request_count = int(payload.get("provider_request_count") or 0)
    native_contract = str(request_meta.get("contract") or "")
    contract_name = _validate_identity(metadata_raw, native_contract)

    try:
        rows_sha = sha256_hex(raw_rows)
        metadata_sha = sha256_hex(metadata_raw)
    except TrainingSourceError:
        # Raw values that cannot be serialized safely (NaN/Inf numbers) are a
        # structural failure: evidence must stay verifiable.
        raise

    excluded_unclosed = 0
    excluded_out_of_range = 0
    normalized: list[dict[str, Any]] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            raise TrainingSourceError("training.sourceUnavailable: raw row is not an object")
        opened = _bar_time(row.get("t"))
        cutoff = int(request["before_time"])
        if opened < cutoff - int(request["limit"]) * DAY_SECONDS or opened >= cutoff:
            excluded_out_of_range += 1
            continue
        try:
            open_price = _price(row.get("o"), "o")
            high = _price(row.get("h"), "h")
            low = _price(row.get("l"), "l")
            close = _price(row.get("c"), "c")
        except TrainingRequestError:
            # Illegal prices stay recorded in raw_rows but cannot be normalized;
            # the response becomes insufficient_evidence below.
            normalized.append({"__invalid__": True, "open_time": opened})
            continue
        if low > min(open_price, close) or high < max(open_price, close):
            normalized.append({"__invalid__": True, "open_time": opened})
            continue
        open_dt = datetime.fromtimestamp(opened, tz=timezone.utc)
        close_dt = datetime.fromtimestamp(opened + DAY_SECONDS, tz=timezone.utc)
        normalized.append({
            "__invalid__": False,
            "open_time": opened,
            "open_time_utc": _iso_utc(open_dt),
            "close_time_utc": _iso_utc(close_dt),
            "available_at_utc": _iso_utc(close_dt),
            "availability_basis": "bar_close_boundary",
            "observed_at_utc": observed,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
        })

    usable = [bar for bar in normalized if not bar["__invalid__"]]
    invalid_prices = len(normalized) - len(usable)
    usable.sort(key=lambda bar: bar["open_time"])
    seen: dict[int, dict[str, Any]] = {}
    duplicate_count = 0
    conflicting_count = 0
    unique_bars: list[dict[str, Any]] = []
    for bar in usable:
        prior = seen.get(bar["open_time"])
        if prior is not None:
            duplicate_count += 1
            if any(bar[field] != prior[field] for field in ("open", "high", "low", "close")):
                conflicting_count += 1
            continue
        seen[bar["open_time"]] = bar
        unique_bars.append(bar)

    identity = {
        "exchange_id": str(payload.get("exchange_id") or request["exchange_id"]),
        "market_type": str(payload.get("market_type") or request["market_type"]),
        "settle": "USDT",
        "contract_type": "direct",
        "canonical_symbol": f"{native_contract.rsplit('_USDT', 1)[0]}/USDT:USDT",
        "native_contract": native_contract,
    }
    klines: list[dict[str, Any]] = []
    for bar in unique_bars:
        klines.append({
            "open_time_utc": bar["open_time_utc"],
            "close_time_utc": bar["close_time_utc"],
            "available_at_utc": bar["available_at_utc"],
            "availability_basis": "bar_close_boundary",
            "observed_at_utc": bar["observed_at_utc"],
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "identity": dict(identity),
        })

    count = len(klines)
    raw_count = len(raw_rows)
    last_close_dt = (
        datetime.fromtimestamp(unique_bars[-1]["open_time"] + DAY_SECONDS, tz=timezone.utc)
        if unique_bars else None
    )
    cutoff = int(request["before_time"])
    window_opens = {
        cutoff - DAY_SECONDS * (index + 1) for index in range(int(request["limit"]))
    }
    missing = len(window_opens - seen.keys()) if count else len(window_opens)
    misaligned = sum(
        1 for left, right in zip(unique_bars, unique_bars[1:])
        if right["open_time"] - left["open_time"] != DAY_SECONDS
    )
    # Continuity verdict (Design §11.4). No caller-supplied listing evidence
    # exists on this production path, so a short return is UNKNOWN, never true.
    status = "ok"
    reason = None
    short_training_history: bool | None = False
    listing_boundary = {"status": "UNKNOWN", "reason": "listing_boundary_unverified"}
    if count < 2:
        status = "insufficient_evidence"
        reason = f"training.unusableSample:count={count},invalid={invalid_prices}"
        short_training_history = None
    elif last_close_dt != datetime.fromtimestamp(cutoff, tz=timezone.utc):
        status = "insufficient_evidence"
        reason = "training.trailingGap:last_close_before_cutoff"
        short_training_history = None
    elif count == int(request["limit"]) and missing == 0 and misaligned == 0 \
            and duplicate_count == 0 and conflicting_count == 0 and invalid_prices == 0:
        short_training_history = False
    elif missing == len(window_opens) or missing > 0:
        # Missing days inside the requested window without a proven listing
        # boundary -> UNKNOWN sample, never short=true.
        status = "insufficient_evidence"
        reason = f"training.unprovenReturn:count={count},missing={missing}"
        short_training_history = None
    else:
        status = "insufficient_evidence"
        reason = (
            f"training.discontinuity:missing={missing},misaligned={misaligned},"
            f"duplicate={duplicate_count},conflicting={conflicting_count},"
            f"invalid={invalid_prices}"
        )
        short_training_history = None

    normalized_sha = sha256_hex(klines)
    coverage = {
        "requested_start_utc": _iso_utc(datetime.fromtimestamp(
            cutoff - int(request["limit"]) * DAY_SECONDS, tz=timezone.utc)),
        "cutoff_utc": _iso_utc(datetime.fromtimestamp(cutoff, tz=timezone.utc)),
        "requested_limit": int(request["limit"]),
        "raw_count": raw_count,
        "actual_count": count,
        "unique_count": count,
        "first_open_utc": klines[0]["open_time_utc"] if klines else None,
        "last_open_utc": klines[-1]["open_time_utc"] if klines else None,
        "last_close_utc": klines[-1]["close_time_utc"] if klines else None,
        "duplicate_count": duplicate_count,
        "conflicting_count": conflicting_count,
        "missing_count": missing,
        "misaligned_count": misaligned,
        "excluded_unclosed_count": excluded_unclosed,
        "excluded_out_of_range_count": excluded_out_of_range,
        "short_training_history": short_training_history,
        "listing_boundary": listing_boundary,
    }

    source = {
        "provider": "native_gate",
        "host": str(request_meta.get("host") or ""),
        "path": str(request_meta.get("path") or ""),
        "query": {
            "contract": native_contract,
            "interval": str(request_meta.get("interval") or "1d"),
            "from": request_meta.get("from"),
            "to": request_meta.get("to"),
        },
        "retrieved_at_utc": observed,
        "raw_rows": raw_rows,
        "raw_rows_sha256": rows_sha,
        "provider_request_count": provider_request_count,
        "request_policy": {"automatic_retries": 0, "pagination": 0, "redirects": 0},
        "metadata": {
            "raw": metadata_raw,
            "sha256": metadata_sha,
            "retrieved_at_utc": str(payload.get("metadata_retrieved_at_utc") or ""),
            "source": {
                "host": str(request_meta.get("host") or ""),
                "path": str(request_meta.get("metadata_path") or ""),
            },
            "settlement_basis": "endpoint_path",
            "quote_basis": "contract_name",
            "metadata_scope": "current_snapshot",
            "historical_identity_validity": "UNKNOWN",
        },
    }

    data = {
        "schema_version": SCHEMA_VERSION,
        "data_mode": DATA_MODE,
        "status": status,
        "reason": reason,
        "market": request["market"],
        "symbol": request["symbol"],
        "timeframe": "1D",
        "count": count,
        "identity": identity,
        "identity_evidence": source["metadata"],
        "source": source,
        "klines": klines,
        "normalized_sha256": normalized_sha,
        "coverage": coverage,
        "synthetic": False,
        "volume_status": "not_provided",
        "backtest_eligible": False,
        "historical_volume_validity": "UNKNOWN",
    }
    if contract_name != native_contract:  # defensive; _validate_identity already raised
        raise TrainingIdentityError("training.identityUnverified")
    return data


def get_training_ohlc(*, symbol: str, before_time: int, limit: int = 365) -> dict:
    """Synchronous read-only training fetch: the service's only I/O entry.

    Fresh public client per call; two bounded GETs maximum; failures map to
    the frozen training error identifiers; the envelope stays the caller's job.
    """
    request = {
        "data_mode": "training_ohlc",
        "market": "Crypto",
        "symbol": str(symbol or "").strip(),
        "timeframe": "1D",
        "exchange_id": "gate",
        "market_type": "swap",
        "before_time": int(before_time),
        "limit": int(limit),
    }
    client = create_native_crypto_client(
        "gate",
        "swap",
        timeout_ms=CryptoPublicConfig.TIMEOUT,
        proxy=CryptoPublicConfig.PROXY,
    )
    try:
        payload = client.fetch_training_ohlc(
            request["symbol"], before_time=request["before_time"], limit=request["limit"]
        )
    except NativeCryptoAPIError as exc:
        text = str(exc)
        if "training.identityUnverified" in text:
            raise TrainingIdentityError(text) from exc
        raise TrainingSourceError(text) from exc
    return normalize_training_payload(
        payload, request, observed_at_utc=_iso_utc(_server_now_utc())
    )
