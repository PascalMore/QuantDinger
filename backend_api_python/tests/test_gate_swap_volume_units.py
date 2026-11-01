"""Offline B-001 regressions; no exchange, cache or service connections."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.data_sources.crypto import CryptoDataSource
from app.data_sources.native_crypto import NativeCryptoAPIError, NativeCryptoPublicClient


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


def _gate_source(monkeypatch, volume="100", multiplier="0.01", quote="123.4500"):
    """Build a synthetic native-to-consumer path with no services or HTTP."""
    native = NativeCryptoPublicClient("gate", "swap")
    parser = native._parse_gate_swap_ohlcv
    metadata = {
        "id": "BTC_USDT", "settle": "USDT",
        "info": {"name": "BTC_USDT", "type": "direct", "quanto_multiplier": multiplier},
        "synthetic": True, "metadata_source": "synthetic",
        "metadata_retrieved_at_utc": "2023-11-15T00:00:00Z",
        "effective_from": "2023-11-14T00:00:00Z",
        "effective_to": "2023-11-16T00:00:00Z",
    }
    monkeypatch.setattr(native, "_parse_gate_swap_ohlcv", lambda rows, **identity: parser(
        rows, metadata=metadata, **identity
    ))
    monkeypatch.setattr(native, "_get", lambda *_args: [
        {"t": 1700000000, "o": "100", "h": "110", "l": "90", "c": "105",
         "v": volume, "sum": quote},
    ])
    source = object.__new__(CryptoDataSource)
    source.exchange = native
    source._allow_public_fallback = True
    source._preferred_public_exchange_id = ""
    source._scoped_exchange_id = "gate"
    source._scoped_market_type = "swap"
    source._symbol_for_scoped_market = lambda symbol: symbol
    source._is_invalid_symbol_cached = lambda _symbol: False
    source.log_result = lambda *_args: None
    fallback = Mock(side_effect=AssertionError("unit errors must not fall back"))
    monkeypatch.setattr(CryptoDataSource, "for_exchange", fallback)
    return source, native


@pytest.mark.parametrize("timeframe", ["15m", "1D"])
@pytest.mark.parametrize("volume,multiplier,expected", [
    ("100", "0.01", 1.0), ("0.000001", "0.001", 1e-9),
])
@pytest.mark.parametrize("quote", ["123.4500", None])
def test_gate_native_to_consumer_preserves_units_projection_and_small_volume(
    timeframe, volume, multiplier, expected, quote, monkeypatch
):
    source, native = _gate_source(monkeypatch, volume, multiplier, quote)
    reader = native.get_gate_swap_volume_evidence

    def evidence_with_non_business_data(*identity):
        return {**reader(*identity), "account": "synthetic-private-field",
                "headers": {"Authorization": "synthetic-not-a-secret"}}

    monkeypatch.setattr(native, "get_gate_swap_volume_evidence", evidence_with_non_business_data)
    rows = source.get_kline("BTC/USDT:USDT", timeframe, 1)
    assert len(rows) == 1
    row = rows[0]
    assert row["volume"] == pytest.approx(expected, rel=1e-12, abs=0)
    assert row["volume"] > 0
    assert row["raw_v_contracts"] == float(volume)
    assert row["quanto_multiplier"] == float(multiplier)
    assert row["quote_volume"] == (None if quote is None else float(quote))
    assert row["volume_unit"] == "base"
    assert row["volume_normalization_version"] == "gate_swap_base_v1"
    assert row["volume_historical_applicability"] == "verified"
    evidence = row["volume_evidence"]
    assert evidence["raw_v_contracts"] == volume
    assert evidence["quanto_multiplier"] == multiplier
    assert evidence["raw_quote_sum"] == quote
    assert evidence["metadata_source"] == "synthetic"
    assert set(evidence) == {
        "exchange_id", "market_type", "settle", "native_contract", "bar_open_utc", "interval",
        "raw_v_contracts", "raw_quote_sum", "quanto_multiplier", "base_volume", "quote_volume",
        "volume_unit", "source_endpoint", "retrieved_at_utc", "metadata_source",
        "metadata_retrieved_at_utc", "effective_from", "effective_to",
        "historical_applicability", "normalization_version",
    }


@pytest.mark.parametrize("changes,code", [
    ({"volume_unit": "contracts"}, "EvidenceMissing"),
    ({"normalization_version": "old"}, "EvidenceMissing"),
    ({"native_contract": "ETH_USDT"}, "EvidenceMissing"),
    ({"interval": "1d"}, "EvidenceMissing"),
    ({"bar_open_utc": "2023-11-14T00:00:00Z"}, "EvidenceMissing"),
    ({"exchange_id": "binance"}, "EvidenceMissing"),
    ({"market_type": "spot"}, "EvidenceMissing"),
    ({"settle": "BTC"}, "EvidenceMissing"),
    ({"historical_applicability": "unknown"}, "HistoricalUnverified"),
    ({"base_volume": 100}, "Invalid"),
])
def test_gate_consumer_rejects_wrong_or_unknown_evidence(changes, code, monkeypatch):
    source, native = _gate_source(monkeypatch)
    reader = native.get_gate_swap_volume_evidence
    monkeypatch.setattr(native, "get_gate_swap_volume_evidence", lambda *identity: {
        **reader(*identity), **changes,
    })
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolume" + code):
        source.get_kline("BTC/USDT:USDT", "15m", 1)


@pytest.mark.parametrize("mixed", [False, True])
def test_gate_old_or_mixed_cache_fixture_cannot_be_relabeled(mixed, monkeypatch):
    """A local bare-six-column cache fixture is UNKNOWN, not a cache migration."""
    source, native = _gate_source(monkeypatch)
    new = native.fetch_ohlcv("BTC/USDT:USDT", "15m", limit=1)
    old = [1700000900000, 100, 110, 90, 105, 100]
    monkeypatch.setattr(source, "_fetch_ohlcv", lambda *_args: new + [old] if mixed else [old])
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeEvidenceMissing"):
        source.get_kline("BTC/USDT:USDT", "15m", 2)


def test_gate_consumer_rejects_missing_evidence_fields(monkeypatch):
    source, native = _gate_source(monkeypatch)
    reader = native.get_gate_swap_volume_evidence

    def incomplete(*identity):
        evidence = reader(*identity)
        evidence.pop("raw_v_contracts")
        return evidence

    monkeypatch.setattr(native, "get_gate_swap_volume_evidence", incomplete)
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeEvidenceMissing"):
        source.get_kline("BTC/USDT:USDT", "15m", 1)


def test_gate_consumer_refuses_resampling_without_full_bar_evidence(monkeypatch):
    source, native = _gate_source(monkeypatch)
    native.timeframes = {"1h": "1h"}
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeEvidenceMissing"):
        source.get_kline("BTC/USDT:USDT", "4H", 1)


@pytest.mark.parametrize("recent", [False, True])
@pytest.mark.parametrize("code", [
    "MetadataMissing", "MetadataInvalid", "HistoricalUnverified", "Invalid", "EvidenceMissing",
])
def test_gate_fallback_catches_also_propagate_unit_errors(code, recent, monkeypatch):
    source, native = _gate_source(monkeypatch)
    error = NativeCryptoAPIError("gate.swapVolume" + code)
    fetch = Mock(side_effect=[[], error] if recent else error)
    monkeypatch.setattr(native, "fetch_ohlcv", fetch)
    with pytest.raises(NativeCryptoAPIError) as caught:
        source._fetch_ohlcv_fallback("BTC/USDT:USDT", "15m", 1, None, "15m")
    assert caught.value is error
    assert fetch.call_count == (2 if recent else 1)
