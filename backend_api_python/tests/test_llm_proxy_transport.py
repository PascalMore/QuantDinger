"""Offline regression coverage for the actual Requests proxy merge boundary."""

import json
from types import SimpleNamespace

import pytest
import requests

from app.services.llm import LLMAPIError, LLMService


HTTP_PROXY = "http://environment-http.invalid:8080"
HTTPS_PROXY = "http://environment-https.invalid:8443"
DEDICATED_PROXY = "http://dedicated.invalid:8888"


@pytest.fixture
def transport(monkeypatch):
    # Keep proxy selection deterministic without reading any real environment file.
    for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy", "REQUEST_METHOD",
        "LLM_PROXY_URL", "LLM_USE_SYSTEM_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTP_PROXY", HTTP_PROXY)
    monkeypatch.setenv("HTTPS_PROXY", HTTPS_PROXY)
    monkeypatch.setenv("NO_PROXY", "api.minimax.cn")
    monkeypatch.setenv("PROXY_URL", "http://exchange-only.invalid:7890")
    state = SimpleNamespace(
        config={"llm": {"proxy_url": "", "use_system_proxy": False}},
        calls=[],
        closed_sessions=[],
        closed_responses=[],
        body=b"{}",
        status_code=200,
        error=None,
    )
    monkeypatch.setattr("app.services.llm.load_addon_config", lambda: state.config)
    original_session_close = requests.sessions.Session.close
    original_response_close = requests.Response.close

    def record_close(session):
        state.closed_sessions.append(session)
        original_session_close(session)

    def record_response_close(response):
        state.closed_responses.append(response)
        original_response_close(response)

    def send(session, request, **kwargs):
        # Requests has already prepared the request and merged environment settings.
        state.calls.append((session, request, kwargs))
        if state.error is not None:
            raise state.error
        response = requests.Response()
        response.status_code = state.status_code
        response.request = request
        response.url = request.url
        response._content = state.body
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.sessions.Session, "send", send)
    monkeypatch.setattr(requests.sessions.Session, "close", record_close)
    monkeypatch.setattr(requests.Response, "close", record_response_close)
    state.service = LLMService(provider="openai")
    return state


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize(
    ("use_system_proxy", "proxy_url", "host", "expected_proxy"),
    [
        (False, "", "api.minimax.cn", None),
        (False, "", "api.openai.com", None),
        (True, "", "api.minimax.cn", None),
        (True, "", "api.openai.com", "environment"),
        (False, DEDICATED_PROXY, "api.minimax.cn", DEDICATED_PROXY),
        (False, DEDICATED_PROXY, "api.openai.com", DEDICATED_PROXY),
        (True, DEDICATED_PROXY, "api.minimax.cn", DEDICATED_PROXY),
        (True, DEDICATED_PROXY, "api.openai.com", DEDICATED_PROXY),
    ],
)
def test_proxy_policy_after_requests_merge(
    transport, stream, scheme, use_system_proxy, proxy_url, host, expected_proxy
):
    transport.config["llm"].update(
        proxy_url=proxy_url, use_system_proxy=use_system_proxy
    )
    headers = {"Authorization": "Bearer fixture-token", "X-Transport-Test": "unchanged"}
    payload = {"model": "fixture-model", "messages": [{"role": "user", "content": "hello"}]}
    url = f"{scheme}://{host}/v1/chat/completions"
    response = transport.service._llm_post(
        url, headers=headers, json_payload=payload, timeout=37, stream=stream
    )
    assert len(transport.calls) == 1
    session, request, kwargs = transport.calls[0]
    try:
        assert request.method == "POST"
        assert request.url == url
        for name, value in headers.items():
            assert request.headers[name] == value
        assert json.loads(request.body) == payload
        assert kwargs["timeout"] == 37
        assert kwargs["stream"] is stream
        assert session.trust_env is (use_system_proxy and not bool(proxy_url))
        proxies = kwargs["proxies"]
        if expected_proxy == "environment":
            assert proxies[scheme] == {"http": HTTP_PROXY, "https": HTTPS_PROXY}[scheme]
        elif expected_proxy is None:
            assert not proxies
        else:
            assert proxies == {"http": DEDICATED_PROXY, "https": DEDICATED_PROXY}
        if stream:
            assert response._quantdinger_llm_session is session
            assert transport.closed_sessions == []
        else:
            assert transport.closed_sessions == [session]
            assert not hasattr(response, "_quantdinger_llm_session")
    finally:
        response.close()
        if stream:
            response._quantdinger_llm_session.close()
    assert transport.closed_sessions == [session]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "exception_type", [requests.exceptions.ConnectionError, requests.exceptions.ProxyError]
)
def test_request_exception_closes_session_and_redacts(transport, stream, exception_type):
    secret = "fixture-private-token"
    transport.error = exception_type(
        f"proxy failed ?api_key={secret} Authorization: Bearer {secret}"
    )
    with pytest.raises(requests.exceptions.ConnectionError) as exc_info:
        transport.service._llm_post(
            "https://api.openai.com/v1/chat/completions",
            headers={}, json_payload={"model": "fixture"}, timeout=19, stream=stream,
        )
    assert len(transport.calls) == 1
    assert transport.closed_sessions == [transport.calls[0][0]]
    assert secret not in str(exc_info.value)
    assert "api_key=<redacted>" in str(exc_info.value)
    assert "Bearer <redacted>" in str(exc_info.value)
    assert "LLM request was routed through a proxy" in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.parametrize("completion", ["done", "early_close", "http_error", "premature_eof"])
def test_existing_stream_consumer_releases_transport_session(transport, completion):
    transport.body = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
    if completion == "done":
        transport.body += b"data: [DONE]\n\n"
    elif completion == "http_error":
        transport.status_code = 503
        transport.body = b'{"error":{"message":"fixture unavailable"}}'
    stream = transport.service._stream_openai_compatible(
        [{"role": "user", "content": "hello"}],
        "fixture-model", 0.7, "fixture-token", "https://api.openai.com/v1", 23,
    )
    try:
        if completion == "http_error":
            with pytest.raises(LLMAPIError) as exc_info:
                next(stream)
            assert exc_info.value.status_code == 503
        else:
            assert next(stream) == "hello"
            session = transport.calls[0][0]
            assert transport.closed_sessions == []
            if completion == "early_close":
                stream.close()
            elif completion == "premature_eof":
                with pytest.raises(LLMAPIError) as exc_info:
                    next(stream)
                assert exc_info.value.error_type == "premature_eof"
            else:
                assert list(stream) == []
        assert len(transport.calls) == 1
        session = transport.calls[0][0]
        assert transport.closed_sessions == [session]
        assert len(transport.closed_responses) == 1
        assert transport.closed_responses[0]._quantdinger_llm_session is session
    finally:
        stream.close()
