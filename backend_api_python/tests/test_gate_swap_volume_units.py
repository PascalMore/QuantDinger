"""Offline B-001 regressions; no exchange, cache or service connections."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.data_sources.crypto import CryptoDataSource
from app.data_sources.native_crypto import NativeCryptoAPIError


@pytest.mark.parametrize(
    "error_code",
    [
        "gate.swapVolumeMetadataMissing",
        "gate.swapVolumeMetadataInvalid",
        "gate.swapVolumeHistoricalUnverified",
        "gate.swapVolumeInvalid",
        "gate.swapVolumeEvidenceMissing",
    ],
)
@pytest.mark.parametrize("historical_window", [False, True])
def test_gate_swap_unit_error_propagates_without_retry_or_fallback(
    error_code, historical_window, monkeypatch
):
    """Exercise real get_kline/_fetch_ohlcv exception handling, not a stub."""
    source = object.__new__(CryptoDataSource)
    source._allow_public_fallback = True
    source._preferred_public_exchange_id = ""
    source._scoped_exchange_id = "gate"
    source._scoped_market_type = "swap"
    error = NativeCryptoAPIError(error_code)
    native_fetch = Mock(side_effect=error)
    source.exchange = SimpleNamespace(
        id="gate", market_type="swap", timeframes={"15m": "15m"},
        enableRateLimit=True, fetch_ohlcv=native_fetch,
    )
    source._symbol_for_scoped_market = lambda _symbol: "BTC/USDT:USDT"
    source._is_invalid_symbol_cached = lambda _symbol: False
    source.log_result = lambda *_args: None
    local_fallback = Mock(return_value=[])
    monkeypatch.setattr(source, "_fetch_ohlcv_fallback", local_fallback)
    public_fallback = Mock(return_value=SimpleNamespace(get_kline=Mock(return_value=[])))
    monkeypatch.setattr(CryptoDataSource, "for_exchange", public_fallback)
    monkeypatch.setattr("app.data_sources.crypto.time.sleep", lambda _seconds: None)
    now = int(datetime.now(timezone.utc).timestamp())
    window = {"before_time": now - 900, "after_time": now - 1800} if historical_window else {}

    # Record all observed behavior before asserting, so a failure identifies the
    # swallowed error and both fallback paths instead of hiding them behind raises().
    caught = None
    rows = None
    try:
        rows = source.get_kline("BTC/USDT:USDT", "15m", 1, **window)
    except NativeCryptoAPIError as exc:
        caught = exc
    observed = {
        "same_error": caught is error,
        "native_calls": native_fetch.call_count,
        "local_fallback_calls": local_fallback.call_count,
        "public_fallback_calls": public_fallback.call_count,
        "returned_rows": rows,
    }
    assert observed == {
        "same_error": True,
        "native_calls": 1,
        "local_fallback_calls": 0,
        "public_fallback_calls": 0,
        "returned_rows": None,
    }
