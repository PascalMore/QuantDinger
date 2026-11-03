import pytest

from app.data_sources.native_crypto import NativeCryptoAPIError, NativeCryptoPublicClient


@pytest.mark.parametrize(
    ("exchange_id", "market_type", "expected"),
    [
        ("binance", "spot", "BTCUSDT"),
        ("bybit", "swap", "BTCUSDT"),
        ("bitget", "spot", "BTCUSDT"),
        ("okx", "swap", "BTC-USDT-SWAP"),
        ("gate", "spot", "BTC_USDT"),
        ("htx", "spot", "btcusdt"),
        ("htx", "swap", "BTC-USDT"),
    ],
)
def test_native_symbol_mapping(exchange_id, market_type, expected):
    client = NativeCryptoPublicClient(exchange_id, market_type)
    assert client._native_symbol("BTC/USDT:USDT") == expected


@pytest.mark.parametrize(
    ("exchange_id", "market_type", "payload", "expected_close"),
    [
        (
            "binance",
            "spot",
            [[1700000000000, "100", "110", "90", "105", "12"]],
            105.0,
        ),
        (
            "okx",
            "swap",
            {"code": "0", "data": [["1700000000000", "100", "110", "90", "106", "13"]]},
            106.0,
        ),
        (
            "bybit",
            "spot",
            {
                "retCode": 0,
                "result": {"list": [["1700000000000", "100", "110", "90", "107", "14"]]},
            },
            107.0,
        ),
        (
            "bitget",
            "swap",
            {"code": "00000", "data": [["1700000000000", "100", "110", "90", "108", "15"]]},
            108.0,
        ),
        (
            "gate",
            "spot",
            [["1700000000", "1000", "109", "110", "90", "100", "16"]],
            109.0,
        ),
        (
            "htx",
            "spot",
            {
                "status": "ok",
                "data": [{"id": 1700000000, "open": 100, "high": 110, "low": 90, "close": 111, "amount": 17}],
            },
            111.0,
        ),
    ],
)
def test_ohlcv_parsing(exchange_id, market_type, payload, expected_close, monkeypatch):
    client = NativeCryptoPublicClient(exchange_id, market_type)
    monkeypatch.setattr(client, "_get", lambda *_args, **_kwargs: payload)

    rows = client.fetch_ohlcv("BTC/USDT", "1m", limit=1)

    assert rows[0][:5] == [1700000000000, 100.0, 110.0, 90.0, expected_close]
    assert rows[0][5] > 0


def test_okx_historical_window_uses_forward_compatible_after_cursor(monkeypatch):
    client = NativeCryptoPublicClient("okx", "spot")
    captured = {}

    def fake_get(url, params):
        captured.update(url=url, params=params)
        return {"code": "0", "data": [["1700000000000", "1", "2", "0.5", "1.5", "3"]]}

    monkeypatch.setattr(client, "_get", fake_get)
    client.fetch_ohlcv("BTC/USDT", "1m", since=1699999880000, limit=2)

    assert captured["url"].endswith("/api/v5/market/history-candles")
    assert captured["params"]["after"] == 1700000000000


def test_bitget_historical_window_is_capped_at_ninety_days(monkeypatch):
    client = NativeCryptoPublicClient("bitget", "swap")
    captured = {}

    def fake_get(url, params):
        captured.update(url=url, params=params)
        return {"code": "00000", "data": []}

    monkeypatch.setattr(client, "_get", fake_get)
    start = 1700000000000
    client.fetch_ohlcv("BTC/USDT", "1d", since=start, limit=1000)

    assert captured["url"].endswith("/api/v3/market/candles")
    assert captured["params"]["endTime"] - start <= 90 * 24 * 60 * 60 * 1000


def test_bitget_weekly_is_resampled_instead_of_sent_as_unsupported_native_interval():
    client = NativeCryptoPublicClient("bitget", "swap")

    assert "1w" not in client.timeframes


def test_market_catalog_exposes_perpetual_alias(monkeypatch):
    client = NativeCryptoPublicClient("binance", "swap")
    monkeypatch.setattr(
        client,
        "_get",
        lambda *_args, **_kwargs: {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                }
            ]
        },
    )

    markets = client.load_markets()

    assert markets["BTC/USDT:USDT"]["contract"] is True
    assert markets["BTC/USDT"]["linear"] is True


def test_bybit_spot_catalog_does_not_send_unsupported_pagination(monkeypatch):
    client = NativeCryptoPublicClient("bybit", "spot")
    captured = []

    def fake_get(_url, params):
        captured.append(params)
        return {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "baseCoin": "BTC",
                        "quoteCoin": "USDT",
                        "status": "Trading",
                    }
                ]
            },
        }

    monkeypatch.setattr(client, "_get", fake_get)
    markets = client.load_markets()

    assert "BTC/USDT" in markets
    assert captured == [{"category": "spot"}]


@pytest.mark.parametrize("market_type", ["spot", "swap"])
def test_bitget_ticker_parses_change_rate_without_unbound_state(market_type, monkeypatch):
    client = NativeCryptoPublicClient("bitget", market_type)
    monkeypatch.setattr(
        client,
        "_get",
        lambda *_args, **_kwargs: {
            "code": "00000",
            "requestTime": 1700000000000,
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "lastPrice": "101.25",
                    "openPrice24h": "100",
                    "highPrice24h": "103",
                    "lowPrice24h": "99",
                    "price24hPcnt": "0.0125",
                    "turnover24h": "2500000",
                    "ts": "1700000000123",
                }
            ],
        },
    )

    ticker = client.fetch_ticker("BTC/USDT")

    assert ticker["last"] == 101.25
    assert ticker["percentage"] == 1.25
    assert ticker["changePercent"] == 1.25
    assert ticker["timestamp"] == 1700000000123


@pytest.mark.parametrize(
    ("metadata", "error_code"),
    [
        ({"name": "BTC_USDT", "type": "direct"}, "gate.swapVolumeMetadataMissing"),
    ],
)
def test_gate_swap_rejects_missing_multiplier(
    metadata, error_code, monkeypatch
):
    """A contract without a multiplier cannot be converted at all."""
    client = NativeCryptoPublicClient("gate", "swap")
    market = {
        "id": "BTC_USDT", "settle": "USDT", "quote": "USDT", "info": metadata,
    }
    client.markets = {"BTC/USDT:USDT": market, "BTC/USDT": market}
    monkeypatch.setattr(
        client,
        "_get",
        lambda *_args, **_kwargs: [
            {"t": 1700000000, "o": "100", "h": "110", "l": "90", "c": "105",
             "v": "100", "sum": "123.45"},
        ],
    )

    with pytest.raises(NativeCryptoAPIError, match=error_code):
        client.fetch_ohlcv("BTC/USDT:USDT", "15m", since=1700000000000, limit=1)


def test_gate_current_snapshot_converts_units_but_stays_unknown(monkeypatch):
    """D-01: a live snapshot is usable, yet it is not historical evidence."""
    client = NativeCryptoPublicClient("gate", "swap")
    market = {
        "id": "BTC_USDT", "settle": "USDT", "quote": "USDT",
        "info": {"name": "BTC_USDT", "type": "direct", "quanto_multiplier": "0.01"},
    }
    client.markets = {"BTC/USDT:USDT": market, "BTC/USDT": market}
    monkeypatch.setattr(
        client,
        "_get",
        lambda *_args, **_kwargs: [
            {"t": 1700000000, "o": "100", "h": "110", "l": "90", "c": "105",
             "v": "100", "sum": "123.45"},
        ],
    )

    rows = client.fetch_ohlcv("BTC/USDT:USDT", "15m", since=1700000000000, limit=1)

    assert rows == [[1700000000000, 100.0, 110.0, 90.0, 105.0, 1.0]]
    evidence = client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700000000000
    )
    assert evidence is not None
    assert evidence["historical_applicability"] == "unknown"
    assert evidence["metadata_source"] != "synthetic"


def _synthetic_gate_metadata(contract="BTC_USDT", multiplier="0.01"):
    return {
        "id": contract, "settle": "USDT",
        "info": {"name": contract, "type": "direct", "quanto_multiplier": multiplier},
        "synthetic": True, "metadata_source": "synthetic",
        "metadata_retrieved_at_utc": "2023-11-15T00:00:00Z",
        "effective_from": "2023-11-14T00:00:00Z",
        "effective_to": "2023-11-16T00:00:00Z",
    }


def _gate_bar(timestamp=1700000000, volume="100", quote="123.4500"):
    return {"t": timestamp, "o": "100", "h": "110", "l": "90", "c": "105",
            "v": volume, "sum": quote}


def _install_synthetic_parser(client, monkeypatch):
    """Inject only at the private parser seam, never through fetch_ohlcv."""
    parser = client._parse_gate_swap_ohlcv

    def parse(rows, **identity):
        return parser(rows, metadata=_synthetic_gate_metadata(
            client._native_symbol(identity["symbol"])
        ), **identity)

    monkeypatch.setattr(client, "_parse_gate_swap_ohlcv", parse)


@pytest.mark.parametrize("timeframe", ["15m", "1d"])
@pytest.mark.parametrize("quote", ["123.4500", None, "0"])
def test_gate_synthetic_base_conversion_and_read_only_raw_evidence(timeframe, quote):
    client = NativeCryptoPublicClient("gate", "swap")
    rows = client._parse_gate_swap_ohlcv(
        [_gate_bar(quote=quote)], symbol="BTC/USDT:USDT", timeframe=timeframe,
        since_ms=1700000000000, metadata=_synthetic_gate_metadata(),
    )
    assert rows == [[1700000000000, 100.0, 110.0, 90.0, 105.0, 1.0]]
    evidence = client.get_gate_swap_volume_evidence("BTC/USDT:USDT", timeframe, rows[0][0])
    assert evidence["raw_v_contracts"] == "100"
    assert evidence["quanto_multiplier"] == "0.01"
    assert evidence["raw_quote_sum"] == quote
    assert evidence["quote_volume"] == (None if quote is None else float(quote))
    assert evidence["metadata_source"] == "synthetic"
    assert evidence["historical_applicability"] == "verified"
    assert evidence["volume_unit"] == "base"
    assert evidence["normalization_version"] == "gate_swap_base_v1"
    evidence["base_volume"] = 999
    assert client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", timeframe, rows[0][0]
    )["base_volume"] == 1.0


@pytest.mark.parametrize("multiplier", [None, "", "bad", "0", "-1", "NaN", "Inf", "-Inf", True])
def test_gate_invalid_multiplier_never_defaults_to_one(multiplier):
    client = NativeCryptoPublicClient("gate", "swap")
    metadata = _synthetic_gate_metadata(multiplier=multiplier)
    code = "MetadataMissing" if multiplier is None else "MetadataInvalid"
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolume" + code):
        client._parse_gate_swap_ohlcv(
            [_gate_bar()], symbol="BTC/USDT:USDT", timeframe="15m", since_ms=None,
            metadata=metadata,
        )
    assert client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", 1700000000000) is None


@pytest.mark.parametrize("field,value", [
    ("type", "inverse"), ("type", None), ("name", "ETH_USDT"), ("name", None),
])
def test_gate_rejects_wrong_native_contract_or_non_direct(field, value):
    client = NativeCryptoPublicClient("gate", "swap")
    metadata = _synthetic_gate_metadata()
    metadata["info"][field] = value
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeMetadataInvalid"):
        client._parse_gate_swap_ohlcv(
            [_gate_bar()], symbol="BTC/USDT:USDT", timeframe="15m", since_ms=None,
            metadata=metadata,
        )


@pytest.mark.parametrize("settle", [None, "BTC", "USD"])
def test_gate_rejects_non_usdt_settlement(settle):
    client = NativeCryptoPublicClient("gate", "swap")
    metadata = _synthetic_gate_metadata()
    metadata["settle"] = settle
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeMetadataInvalid"):
        client._parse_gate_swap_ohlcv(
            [_gate_bar()], symbol="BTC/USDT:USDT", timeframe="15m", since_ms=None,
            metadata=metadata,
        )


@pytest.mark.parametrize("volume", [None, "", "bad", "-1", "NaN", "Inf", "-Inf", True])
def test_gate_rejects_missing_non_positive_or_non_finite_raw_v(volume):
    client = NativeCryptoPublicClient("gate", "swap")
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeInvalid"):
        client._parse_gate_swap_ohlcv(
            [_gate_bar(volume=volume)], symbol="BTC/USDT:USDT", timeframe="15m",
            since_ms=None, metadata=_synthetic_gate_metadata(),
        )


_GATE_SWAP_EVIDENCE_FIELDS = {
    "exchange_id", "market_type", "settle", "native_contract", "bar_open_utc",
    "interval", "raw_v_contracts", "raw_quote_sum", "quanto_multiplier",
    "base_volume", "quote_volume", "volume_unit", "source_endpoint",
    "retrieved_at_utc", "metadata_source", "metadata_retrieved_at_utc",
    "effective_from", "effective_to", "historical_applicability",
    "normalization_version",
}


def test_gate_zero_volume_bar_is_a_valid_zero_trade_bar():
    """D1: a real zero-trade bar parses with base volume exactly 0.0."""
    client = NativeCryptoPublicClient("gate", "swap")
    rows = client._parse_gate_swap_ohlcv(
        [_gate_bar(volume="0", quote="0")], symbol="BTC/USDT:USDT", timeframe="15m",
        since_ms=1700000000000, metadata=_synthetic_gate_metadata(),
    )
    assert rows == [[1700000000000, 100.0, 110.0, 90.0, 105.0, 0.0]]
    assert len(rows[0]) == 6
    evidence = client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", rows[0][0])
    assert evidence is not None
    assert set(evidence) == _GATE_SWAP_EVIDENCE_FIELDS
    assert evidence["base_volume"] == 0.0
    assert evidence["raw_v_contracts"] == "0"
    assert evidence["quote_volume"] == 0.0
    assert evidence["volume_unit"] == "base"
    assert evidence["normalization_version"] == "gate_swap_base_v1"
    assert evidence["historical_applicability"] == "verified"


def test_gate_zero_volume_bar_in_current_snapshot_stays_unknown(monkeypatch):
    """D1: zero volume does not upgrade historical applicability."""
    client = NativeCryptoPublicClient("gate", "swap")
    market = {
        "id": "BTC_USDT", "settle": "USDT", "quote": "USDT",
        "info": {"name": "BTC_USDT", "type": "direct", "quanto_multiplier": "0.01"},
    }
    client.markets = {"BTC/USDT:USDT": market, "BTC/USDT": market}
    monkeypatch.setattr(
        client,
        "_get",
        lambda *_args, **_kwargs: [
            {"t": 1700000000, "o": "100", "h": "110", "l": "90", "c": "105",
             "v": "0", "sum": "0"},
        ],
    )

    rows = client.fetch_ohlcv("BTC/USDT:USDT", "15m", since=1700000000000, limit=1)

    assert rows == [[1700000000000, 100.0, 110.0, 90.0, 105.0, 0.0]]
    evidence = client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700000000000
    )
    assert evidence is not None
    assert evidence["historical_applicability"] == "unknown"
    assert evidence["metadata_source"] != "synthetic"
    assert evidence["base_volume"] == 0.0


@pytest.mark.parametrize("volume", ["-0", "-0.0"])
def test_gate_negative_zero_volume_behaves_like_zero_volume(volume):
    """D1: negative zero is zero by numeric equality, with no sign-bit rule."""
    client = NativeCryptoPublicClient("gate", "swap")
    rows = client._parse_gate_swap_ohlcv(
        [_gate_bar(volume=volume, quote="0")], symbol="BTC/USDT:USDT", timeframe="15m",
        since_ms=1700000000000, metadata=_synthetic_gate_metadata(),
    )
    assert rows == [[1700000000000, 100.0, 110.0, 90.0, 105.0, 0.0]]
    evidence = client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", rows[0][0])
    assert evidence is not None
    assert evidence["base_volume"] == 0.0
    assert evidence["quote_volume"] == 0.0


def test_gate_zero_volume_still_requires_a_multiplier():
    """D1: the multiplier check runs before any zero-volume semantics."""
    client = NativeCryptoPublicClient("gate", "swap")
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeMetadataMissing"):
        client._parse_gate_swap_ohlcv(
            [_gate_bar(volume="0")], symbol="BTC/USDT:USDT", timeframe="15m",
            since_ms=None, metadata={**_synthetic_gate_metadata(),
                                      "info": {"name": "BTC_USDT", "type": "direct",
                                               "quanto_multiplier": None}},
        )


@pytest.mark.parametrize("volume", ["1e-300", "0"])
def test_gate_non_positive_multiplier_still_wins_over_zero_volume(volume):
    """D1: a non-positive multiplier is rejected before the volume is read."""
    client = NativeCryptoPublicClient("gate", "swap")
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeMetadataInvalid"):
        client._parse_gate_swap_ohlcv(
            [_gate_bar(volume=volume)], symbol="BTC/USDT:USDT", timeframe="15m",
            since_ms=None, metadata=_synthetic_gate_metadata(multiplier="0.0"),
        )


@pytest.mark.parametrize("volume,multiplier", [("1e308", "10"), ("1e-300", "1e-300")])
def test_gate_rejects_multiplication_overflow_and_underflow(volume, multiplier):
    client = NativeCryptoPublicClient("gate", "swap")
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeInvalid"):
        client._parse_gate_swap_ohlcv(
            [_gate_bar(volume=volume)], symbol="BTC/USDT:USDT", timeframe="15m",
            since_ms=None, metadata=_synthetic_gate_metadata(multiplier=multiplier),
        )


@pytest.mark.parametrize("quote", ["", "bad", "-1", "NaN", "Inf", "-Inf", True])
def test_gate_invalid_sum_is_not_silently_zeroed(quote):
    client = NativeCryptoPublicClient("gate", "swap")
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeInvalid"):
        client._parse_gate_swap_ohlcv(
            [_gate_bar(quote=quote)], symbol="BTC/USDT:USDT", timeframe="15m",
            since_ms=None, metadata=_synthetic_gate_metadata(),
        )


@pytest.mark.parametrize("override", [
    {"effective_from": None}, {"effective_to": None}, {"effective_to": "bad"},
    {"effective_from": "2023-11-14T23:00:00Z"},
    {"effective_to": "2023-11-14T22:14:00Z"},
    {"effective_from": "2023-11-14T00:00:00"},
])
def test_gate_synthetic_interval_must_cover_entire_bar(override):
    client = NativeCryptoPublicClient("gate", "swap")
    metadata = {**_synthetic_gate_metadata(), **override}
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeHistoricalUnverified"):
        client._parse_gate_swap_ohlcv(
            [_gate_bar()], symbol="BTC/USDT:USDT", timeframe="15m", since_ms=None,
            metadata=metadata,
        )


@pytest.mark.parametrize("override", [
    {"synthetic": False},
])
def test_gate_non_synthetic_metadata_is_current_not_verified(override):
    """D-01: without the synthetic marker the snapshot stays honestly unknown."""
    client = NativeCryptoPublicClient("gate", "swap")
    metadata = {**_synthetic_gate_metadata(), **override}
    rows = client._parse_gate_swap_ohlcv(
        [_gate_bar()], symbol="BTC/USDT:USDT", timeframe="15m", since_ms=None,
        metadata=metadata,
    )
    assert rows == [[1700000000000, 100.0, 110.0, 90.0, 105.0, 1.0]]
    evidence = client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", rows[0][0])
    assert evidence is not None
    assert evidence["historical_applicability"] == "unknown"


def test_gate_synthetic_marker_with_foreign_source_stays_rejected():
    """A synthetic marker paired with a non-synthetic source is inconsistent."""
    client = NativeCryptoPublicClient("gate", "swap")
    metadata = {**_synthetic_gate_metadata(), "metadata_source": "current_snapshot"}
    with pytest.raises(NativeCryptoAPIError, match="gate.swapVolumeHistoricalUnverified"):
        client._parse_gate_swap_ohlcv(
            [_gate_bar()], symbol="BTC/USDT:USDT", timeframe="15m", since_ms=None,
            metadata=metadata,
        )


def test_gate_public_snapshot_does_not_import_synthetic_history_from_info(monkeypatch):
    """A provider payload that mimics the fixture cannot become verified."""
    client = NativeCryptoPublicClient("gate", "swap")
    metadata = _synthetic_gate_metadata()
    market = {"id": "BTC_USDT", "settle": "USDT", "quote": "USDT", "info": metadata["info"]}
    market["info"].update(metadata)
    client.markets = {"BTC/USDT:USDT": market}
    monkeypatch.setattr(client, "_get", lambda *_args: [_gate_bar()])
    rows = client.fetch_ohlcv("BTC/USDT:USDT", "15m", limit=1)
    assert rows == [[1700000000000, 100.0, 110.0, 90.0, 105.0, 1.0]]
    evidence = client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", rows[0][0])
    assert evidence is not None
    assert evidence["historical_applicability"] == "unknown"


@pytest.mark.parametrize("failure", ["network", "empty", "invalid", "timeframe", "symbol"])
def test_gate_failed_or_empty_next_fetch_cannot_reuse_old_evidence(failure, monkeypatch):
    client = NativeCryptoPublicClient("gate", "swap")
    _install_synthetic_parser(client, monkeypatch)
    monkeypatch.setattr(client, "_get", lambda *_args: [_gate_bar()])
    client.fetch_ohlcv("BTC/USDT:USDT", "15m", limit=1)
    assert client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", 1700000000000)

    def next_page(*_args):
        if failure == "network":
            raise NativeCryptoAPIError("offline network failure")
        return [] if failure == "empty" else [_gate_bar(volume="NaN")]

    monkeypatch.setattr(client, "_get", next_page)
    if failure == "empty":
        assert client.fetch_ohlcv("BTC/USDT:USDT", "15m", limit=1) == []
    else:
        with pytest.raises((NativeCryptoAPIError, ValueError)):
            client.fetch_ohlcv(
                "invalid" if failure == "symbol" else "BTC/USDT:USDT",
                "invalid" if failure == "timeframe" else "15m", limit=1,
            )
    assert client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", 1700000000000) is None


def test_gate_evidence_is_only_for_returned_page_and_exact_identity(monkeypatch):
    client = NativeCryptoPublicClient("gate", "swap")
    _install_synthetic_parser(client, monkeypatch)
    monkeypatch.setattr(client, "_get", lambda *_args: [_gate_bar(), _gate_bar(1700000900)])
    rows = client.fetch_ohlcv("BTC/USDT:USDT", "15m", since=1700000900000, limit=1)
    assert [row[0] for row in rows] == [1700000900000]
    assert client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", 1700000000000) is None
    assert client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "1d", rows[0][0]) is None
    assert client.get_gate_swap_volume_evidence("ETH/USDT:USDT", "15m", rows[0][0]) is None
    assert client.get_gate_swap_volume_evidence("BTC/USDT", "15m", rows[0][0]) is None
    assert client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", rows[0][0])
    monkeypatch.setattr(client, "_get", lambda *_args: [_gate_bar()])
    client.fetch_ohlcv("ETH/USDT:USDT", "15m", limit=1)
    assert client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", rows[0][0]) is None


def test_gate_thread_local_evidence_with_interleaved_symbols(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client = NativeCryptoPublicClient("gate", "swap")
    _install_synthetic_parser(client, monkeypatch)
    monkeypatch.setattr(client, "_get", lambda *_args: [_gate_bar()])
    barrier = Barrier(2)

    def fetch_and_read(symbol, other):
        rows = client.fetch_ohlcv(symbol, "15m", limit=1)
        barrier.wait(timeout=5)
        assert client.get_gate_swap_volume_evidence(other, "15m", rows[0][0]) is None
        return client.get_gate_swap_volume_evidence(symbol, "15m", rows[0][0])

    with ThreadPoolExecutor(max_workers=2) as pool:
        btc = pool.submit(fetch_and_read, "BTC/USDT:USDT", "ETH/USDT:USDT")
        eth = pool.submit(fetch_and_read, "ETH/USDT:USDT", "BTC/USDT:USDT")
        assert btc.result()["native_contract"] == "BTC_USDT"
        assert eth.result()["native_contract"] == "ETH_USDT"
    assert client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", 1700000000000) is None


def test_gate_reload_replaces_multiplier_and_invalidates_existing_evidence(monkeypatch):
    client = NativeCryptoPublicClient("gate", "swap")
    calls = []

    def market_rows():
        calls.append(True)
        return [{"id": "BTC_USDT", "base": "BTC", "quote": "USDT", "settle": "USDT",
                 "info": {"name": "BTC_USDT", "type": "direct",
                          "quanto_multiplier": "0.01" if len(calls) == 1 else "0.02"}}]

    monkeypatch.setattr(client, "_fetch_market_rows", market_rows)
    client.load_markets()
    first = client._gate_swap_contract_metadata("BTC_USDT")
    client._parse_gate_swap_ohlcv(
        [_gate_bar()], symbol="BTC/USDT:USDT", timeframe="15m", since_ms=None,
        metadata={**_synthetic_gate_metadata(), **first},
    )
    assert client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", 1700000000000)
    client.load_markets(reload=True)
    assert client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", 1700000000000) is None
    second = client._gate_swap_contract_metadata("BTC_USDT")
    assert second["info"]["quanto_multiplier"] == "0.02"
    rows = client._parse_gate_swap_ohlcv(
        [_gate_bar()], symbol="BTC/USDT:USDT", timeframe="15m", since_ms=None,
        metadata={**_synthetic_gate_metadata(), **second},
    )
    assert rows[0][5] == 2.0
    assert len(calls) == 2


@pytest.mark.parametrize("exchange,market_type,payload,path,params,volume", [
    ("binance", "spot", [[1700000000000, "1", "2", "0.5", "1.5", "12"]],
     "/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 1}, 12.0),
    ("binance", "swap", [[1700000000000, "1", "2", "0.5", "1.5", "13"]],
     "/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 1}, 13.0),
    ("okx", "spot", {"data": [["1700000000000", "1", "2", "0.5", "1.5", "14"]]},
     "/api/v5/market/candles", {"instId": "BTC-USDT", "bar": "1m", "limit": 1}, 14.0),
    ("okx", "swap", {"data": [["1700000000000", "1", "2", "0.5", "1.5", "15"]]},
     "/api/v5/market/candles", {"instId": "BTC-USDT-SWAP", "bar": "1m", "limit": 1}, 15.0),
    ("bybit", "spot", {"result": {"list": [["1700000000000", "1", "2", "0.5", "1.5", "16"]]}},
     "/v5/market/kline", {"category": "spot", "symbol": "BTCUSDT", "interval": "1", "limit": 1}, 16.0),
    ("bybit", "swap", {"result": {"list": [["1700000000000", "1", "2", "0.5", "1.5", "17"]]}},
     "/v5/market/kline", {"category": "linear", "symbol": "BTCUSDT", "interval": "1", "limit": 1}, 17.0),
    ("bitget", "spot", {"data": [["1700000000000", "1", "2", "0.5", "1.5", "18"]]},
     "/api/v3/market/candles", {"category": "SPOT", "symbol": "BTCUSDT", "interval": "1m", "limit": 1}, 18.0),
    ("bitget", "swap", {"data": [["1700000000000", "1", "2", "0.5", "1.5", "19"]]},
     "/api/v3/market/candles", {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "interval": "1m", "limit": 1}, 19.0),
    ("gate", "spot", [["1700000000", "999999", "1.5", "2", "0.5", "1", "20"]],
     "/api/v4/spot/candlesticks", {"currency_pair": "BTC_USDT", "interval": "1m", "limit": 1}, 20.0),
    ("htx", "spot", {"data": [{"id": 1700000000, "open": "1", "high": "2", "low": "0.5", "close": "1.5", "amount": "21"}]},
     "/market/history/kline", {"symbol": "btcusdt", "period": "1min", "size": 1}, 21.0),
    ("htx", "swap", {"data": [{"id": 1700000000, "open": "1", "high": "2", "low": "0.5", "close": "1.5", "amount": "22"}]},
     "/linear-swap-ex/market/history/kline", {"contract_code": "BTC-USDT", "period": "1min", "size": 1}, 22.0),
])
def test_non_gate_swap_request_and_six_column_volume_regression(
    exchange, market_type, payload, path, params, volume, monkeypatch
):
    client = NativeCryptoPublicClient(exchange, market_type)
    calls = []

    def get(url, query):
        calls.append((url, query))
        return payload

    def unexpected_metadata(*_args):
        pytest.fail("non-Gate-swap must not request multiplier metadata")

    monkeypatch.setattr(client, "_get", get)
    monkeypatch.setattr(client, "load_markets", unexpected_metadata)
    assert client.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=9999) == [
        [1700000000000, 1.0, 2.0, 0.5, 1.5, volume],
    ]
    assert len(calls) == 1
    assert calls[0][0].endswith(path)
    sent = calls[0][1]
    for key, value in params.items():
        if key not in ("limit", "size"):
            assert sent[key] == value
    # Per-venue single-request page caps are unchanged; okx candles are capped at
    # 300 by the exchange, others at 1000.
    cap = {"binance": 1000, "okx": 300, "bybit": 1000, "bitget": 1000, "htx": 1000,
           "gate": 1000}[exchange]
    if (exchange, market_type) in (("okx", "spot"), ("okx", "swap")):
        cap = 300
    if exchange == "htx":
        assert sent["size"] == cap
    else:
        assert sent["limit"] == cap


def test_fetch_ohlcv_restores_final_limit_on_all_paths(monkeypatch):
    """F-01: the public entry re-applies its ascending ``[:limit]`` count bound
    on every venue/path (4111d45 contract: oldest-kept head slice, no tail
    reinterpretation), and Gate evidence keeps only the finally returned bars."""
    three_ms = [1700000000000, 1700000060000, 1700000120000]

    # nonGate (binance spot): transport over-returns 3 bars, limit=1.
    binance = NativeCryptoPublicClient("binance", "spot")
    monkeypatch.setattr(
        binance, "_get",
        lambda *_a, **_k: [[ts, "100", "110", "90", "105", "12"] for ts in three_ms],
    )
    assert binance.fetch_ohlcv("BTC/USDT", "1m", limit=1) == [
        [1700000000000, 100.0, 110.0, 90.0, 105.0, 12.0],
    ]

    # Gate spot: same over-return with the spot seven-column payload shape.
    gate_spot = NativeCryptoPublicClient("gate", "spot")
    monkeypatch.setattr(
        gate_spot, "_get",
        lambda *_a, **_k: [
            [str(ts // 1000), "0", "109", "110", "90", "100", "16"] for ts in three_ms
        ],
    )
    rows = gate_spot.fetch_ohlcv("BTC/USDT", "1m", limit=1)
    assert len(rows) == 1
    assert rows[0][0] == 1700000000000

    # Private synthetic Gate swap seam (same fixture as the volume-unit tests).
    gate_swap = NativeCryptoPublicClient("gate", "swap")
    _install_synthetic_parser(gate_swap, monkeypatch)
    monkeypatch.setattr(
        gate_swap, "_get",
        lambda *_a, **_k: [
            _gate_bar(ts) for ts in (1700000000, 1700000900, 1700001800)
        ],
    )
    rows = gate_swap.fetch_ohlcv("BTC/USDT:USDT", "15m", limit=1)
    assert rows == [[1700000000000, 100.0, 110.0, 90.0, 105.0, 1.0]]
    # Bars cut by the limit must not remain hittable through the evidence seam.
    assert gate_swap.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", 1700000000000)
    assert gate_swap.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700000900000) is None
    assert gate_swap.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700001800000) is None


def test_gate_multibatch_same_identity_accumulates_all_bar_evidence():
    """V1.9-01: three same-identity batches keep every bar's evidence (§14.1).

    This is the deterministic fe16b257 failure mode: pages beyond the first
    used to reset ``state.bars``, so earlier-page evidence was lost and the
    consumer raised ``gate.swapVolumeEvidenceMissing``.
    """
    client = NativeCryptoPublicClient("gate", "swap")
    stamps = [1700000000 + i * 900 for i in range(6)]
    client._parse_gate_swap_ohlcv(
        [_gate_bar(stamps[0]), _gate_bar(stamps[1])], symbol="BTC/USDT:USDT",
        timeframe="15m", since_ms=stamps[0] * 1000, metadata=_synthetic_gate_metadata(),
    )
    client._parse_gate_swap_ohlcv(
        [_gate_bar(stamps[2]), _gate_bar(stamps[3])], symbol="BTC/USDT:USDT",
        timeframe="15m", since_ms=stamps[2] * 1000, metadata=_synthetic_gate_metadata(),
    )
    client._parse_gate_swap_ohlcv(
        [_gate_bar(stamps[4]), _gate_bar(stamps[5])], symbol="BTC/USDT:USDT",
        timeframe="15m", since_ms=stamps[4] * 1000, metadata=_synthetic_gate_metadata(),
    )
    for stamp in stamps:
        evidence = client.get_gate_swap_volume_evidence(
            "BTC/USDT:USDT", "15m", stamp * 1000
        )
        assert evidence is not None, f"page-1..3 bar {stamp} evidence lost"
        assert evidence["raw_v_contracts"] == "100"


def test_gate_symbol_change_resets_accumulated_evidence():
    """V1.9-02: an identity field change resets state; old evidence is gone."""
    client = NativeCryptoPublicClient("gate", "swap")
    client._parse_gate_swap_ohlcv(
        [_gate_bar(1700000000), _gate_bar(1700000900)], symbol="BTC/USDT:USDT",
        timeframe="15m", since_ms=1700000000000, metadata=_synthetic_gate_metadata(),
    )
    assert client.get_gate_swap_volume_evidence("BTC/USDT:USDT", "15m", 1700000000000)

    eth_metadata = _synthetic_gate_metadata(contract="ETH_USDT")
    client._parse_gate_swap_ohlcv(
        [_gate_bar(1700000000)], symbol="ETH/USDT:USDT", timeframe="15m",
        since_ms=1700000000000, metadata=eth_metadata,
    )
    assert client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700000000000) is None
    assert client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700000900000) is None
    assert client.get_gate_swap_volume_evidence(
        "ETH/USDT:USDT", "15m", 1700000000000)["native_contract"] == "ETH_USDT"


def test_gate_multibatch_same_timestamp_new_batch_overwrites_old_entry():
    """V1.9-04: merging a later page overwrites same-key entries only."""
    client = NativeCryptoPublicClient("gate", "swap")
    client._parse_gate_swap_ohlcv(
        [_gate_bar(1700000000, volume="100")], symbol="BTC/USDT:USDT",
        timeframe="15m", since_ms=1700000000000, metadata=_synthetic_gate_metadata(),
    )
    client._parse_gate_swap_ohlcv(
        [_gate_bar(1700000000, volume="200"), _gate_bar(1700000900)],
        symbol="BTC/USDT:USDT", timeframe="15m", since_ms=1700000900000,
        metadata=_synthetic_gate_metadata(),
    )
    overwritten = client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700000000000)
    untouched = client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700000900000)
    assert overwritten["raw_v_contracts"] == "200"
    assert overwritten["base_volume"] == 2.0
    assert untouched["raw_v_contracts"] == "100"


def test_gate_zero_volume_bar_evidence_survives_multiple_pages():
    """V1.9-05: v=0 bars keep their evidence across pages (§13 interplay)."""
    client = NativeCryptoPublicClient("gate", "swap")
    client._parse_gate_swap_ohlcv(
        [_gate_bar(1700000000, volume="0", quote="0"), _gate_bar(1700000900)],
        symbol="BTC/USDT:USDT", timeframe="15m", since_ms=1700000000000,
        metadata=_synthetic_gate_metadata(),
    )
    client._parse_gate_swap_ohlcv(
        [_gate_bar(1700001800)], symbol="BTC/USDT:USDT", timeframe="15m",
        since_ms=1700001800000, metadata=_synthetic_gate_metadata(),
    )
    zero_evidence = client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700000000000)
    later_evidence = client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700001800000)
    assert zero_evidence["base_volume"] == 0.0
    assert zero_evidence["raw_v_contracts"] == "0"
    assert zero_evidence["historical_applicability"] == "verified"
    assert set(zero_evidence) == _GATE_SWAP_EVIDENCE_FIELDS
    assert later_evidence["raw_v_contracts"] == "100"


def test_gate_evidence_capacity_cap_evicts_oldest_bars(monkeypatch):
    """V1.9-03: the 20000-bar cap evicts the oldest entries by timestamp."""
    client = NativeCryptoPublicClient("gate", "swap")
    from app.data_sources import native_crypto as native_crypto_module
    monkeypatch.setattr(
        native_crypto_module, "_GATE_SWAP_EVIDENCE_MAX_BARS", 3,
    )
    client._parse_gate_swap_ohlcv(
        [_gate_bar(1700000000), _gate_bar(1700000900), _gate_bar(1700001800)],
        symbol="BTC/USDT:USDT", timeframe="15m", since_ms=1700000000000,
        metadata=_synthetic_gate_metadata(),
    )
    client._parse_gate_swap_ohlcv(
        [_gate_bar(1700002700)], symbol="BTC/USDT:USDT", timeframe="15m",
        since_ms=1700002700000, metadata=_synthetic_gate_metadata(),
    )
    assert client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700000000000) is None
    # Exactly the oldest bar is evicted; everything within the cap survives.
    assert client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700000900000)["raw_v_contracts"] == "100"
    assert client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700001800000)["raw_v_contracts"] == "100"
    assert client.get_gate_swap_volume_evidence(
        "BTC/USDT:USDT", "15m", 1700002700000)["raw_v_contracts"] == "100"
