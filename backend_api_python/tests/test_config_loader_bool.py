"""Regression coverage for boolean environment-config parsing.

``_convert_config_value`` used ``bool(value) or value == '1' or ...`` which
turned every non-empty string into ``True``. Values such as ``false``, ``0``,
``off`` or ``no`` therefore enabled boolean flags that the operator disabled.

The expected semantics are the repository-wide truthy contract used by
``LLMService._truthy``: only ``1/true/yes/on`` (case-insensitive, surrounding
whitespace ignored) are truthy; everything else is falsy.
"""

import os

import pytest

# Isolated dotenv target: a file named ".env" inside a directory that does not
# exist, so no real environment file is loaded while these tests run.
_ISOLATED_ENV_FILE = os.path.join(
    os.path.dirname(__file__), ".nonexistent_env_dir_for_config_loader_bool_tests", ".env"
)

# Apply the isolation before the app package is imported (importing it triggers
# the dotenv load) and restore the previous value right afterwards, so the
# change does not leak into other test modules.
_PREVIOUS_ENV_FILE = os.environ.get("QUANTDINGER_ENV_FILE")
os.environ["QUANTDINGER_ENV_FILE"] = _ISOLATED_ENV_FILE

from app.services.llm import LLMService  # noqa: E402
from app.utils import config_loader  # noqa: E402
from app.utils.config_loader import _convert_config_value, load_addon_config  # noqa: E402

if _PREVIOUS_ENV_FILE is None:
    os.environ.pop("QUANTDINGER_ENV_FILE", None)
else:
    os.environ["QUANTDINGER_ENV_FILE"] = _PREVIOUS_ENV_FILE

_TRUTHY_VALUES = ["1", "true", "TRUE", "True", "tRuE", "yes", "YES", "on", "ON", " true ", "\t1\n"]
_FALSY_VALUES = ["0", "false", "FALSE", "False", "fAlSe", "no", "NO", "off", "OFF", "  ", ""]
_UNKNOWN_VALUES = ["maybe", "2", "null", "none", "enabled", "truthy"]


@pytest.fixture(autouse=True)
def isolated_env_and_config_cache(monkeypatch):
    """Isolate the dotenv target and the process-level config cache.

    ``monkeypatch`` restores ``QUANTDINGER_ENV_FILE`` afterwards, so other
    test modules keep the environment they expect.
    """
    monkeypatch.setenv("QUANTDINGER_ENV_FILE", _ISOLATED_ENV_FILE)

    saved_cache = config_loader._config_cache
    saved_env_loaded = config_loader._env_loaded
    config_loader._config_cache = None
    # Prevent the config loader from touching any dotenv file during the test.
    config_loader._env_loaded = True
    try:
        yield
    finally:
        config_loader._config_cache = saved_cache
        config_loader._env_loaded = saved_env_loaded


def test_env_file_isolation_target_does_not_exist():
    """Guard: the isolation path must not be a real environment file."""
    assert not os.path.exists(_ISOLATED_ENV_FILE)
    assert os.environ["QUANTDINGER_ENV_FILE"] == _ISOLATED_ENV_FILE


@pytest.mark.parametrize("value", _TRUTHY_VALUES)
def test_truthy_strings_are_parsed_as_true(value):
    assert _convert_config_value(value, "bool") is True


@pytest.mark.parametrize("value", _FALSY_VALUES)
def test_falsy_strings_are_parsed_as_false(value):
    assert _convert_config_value(value, "bool") is False


@pytest.mark.parametrize("value", _UNKNOWN_VALUES)
def test_unknown_strings_are_parsed_as_false(value):
    assert _convert_config_value(value, "bool") is False


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        (None, False),
    ],
)
def test_typed_bool_and_numeric_inputs_match_truthy_helper(value, expected):
    """Typed booleans and 0/1 keep the semantics of the shared truthy helper."""
    assert _convert_config_value(value, "bool") is expected
    assert LLMService._truthy(value) is expected


@pytest.mark.parametrize(
    "value_type,value,expected",
    [
        ("int", "42", 42),
        ("int", "", 0),
        ("int", "not-an-int", 0),
        ("float", "1.5", 1.5),
        ("float", "", 0.0),
        ("float", "not-a-float", 0.0),
        ("json", '{"a": 1}', {"a": 1}),
        ("json", "", {}),
        ("json", "not-json", {}),
        ("string", "plain", "plain"),
        ("string", "", ""),
    ],
)
def test_non_bool_conversions_are_unchanged(value_type, value, expected):
    """Only the bool branch changed; other type conversions stay as they were."""
    assert _convert_config_value(value, value_type) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("False", False),
        ("false", False),
        ("0", False),
        ("off", False),
        ("no", False),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_load_addon_config_chain_reaches_llm_service(monkeypatch, raw, expected):
    """The real load_addon_config -> LLMService._llm_use_system_proxy chain."""
    monkeypatch.setenv("LLM_USE_SYSTEM_PROXY", raw)

    config = load_addon_config()

    assert config["llm"]["use_system_proxy"] is expected
    assert LLMService()._llm_use_system_proxy() is expected
