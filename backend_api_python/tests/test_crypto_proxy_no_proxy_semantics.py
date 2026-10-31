"""Regression tests for the crypto proxy / NO_PROXY semantics.

PROXY_URL is an explicit forced-proxy switch. When it is not configured the
platform must NOT pass an explicit ``proxies=`` argument to requests, because an
explicit ``proxies=`` takes precedence over NO_PROXY matching and would route
every exchange request through the proxy unconditionally.
"""
import pytest

from app.config import CryptoPublicConfig
from app.data_sources.native_crypto import create_native_crypto_client

_PROXY_ENV_KEYS = ("PROXY_URL", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")


@pytest.fixture
def clean_proxy_env(monkeypatch):
    """Remove every proxy environment variable for the duration of a test."""
    for key in _PROXY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_proxy_url_unset_ignores_https_proxy(clean_proxy_env):
    clean_proxy_env.setenv("HTTPS_PROXY", "http://172.25.240.1:7897")
    assert CryptoPublicConfig.PROXY == ""


def test_proxy_url_unset_ignores_http_proxy(clean_proxy_env):
    clean_proxy_env.setenv("HTTP_PROXY", "http://172.25.240.1:7897")
    assert CryptoPublicConfig.PROXY == ""


def test_proxy_url_unset_ignores_all_proxy(clean_proxy_env):
    clean_proxy_env.setenv("ALL_PROXY", "http://172.25.240.1:7897")
    assert CryptoPublicConfig.PROXY == ""


def test_proxy_url_set_returns_forced_proxy(clean_proxy_env):
    clean_proxy_env.setenv("PROXY_URL", "http://host.docker.internal:7890")
    clean_proxy_env.setenv("HTTPS_PROXY", "http://172.25.240.1:7897")
    assert CryptoPublicConfig.PROXY == "http://host.docker.internal:7890"


def test_proxy_url_explicit_empty_string_is_treated_as_unset(clean_proxy_env):
    clean_proxy_env.setenv("PROXY_URL", "")
    clean_proxy_env.setenv("HTTPS_PROXY", "http://172.25.240.1:7897")
    assert CryptoPublicConfig.PROXY == ""


def test_no_proxy_environment_returns_empty(clean_proxy_env):
    assert CryptoPublicConfig.PROXY == ""


def test_client_does_not_force_proxies_when_proxy_url_unset(clean_proxy_env):
    clean_proxy_env.setenv("HTTPS_PROXY", "http://172.25.240.1:7897")
    client = create_native_crypto_client("binance", "spot", proxy=CryptoPublicConfig.PROXY)
    assert client._proxies is None
