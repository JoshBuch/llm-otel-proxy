"""Cross-cutting integration tests for the full proxy app.

These tests exercise behaviors that span the entire request/response lifecycle
(routing, header forwarding, body passthrough, span emission) using the
combined app from proxy/server.py.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from llm_otel_sidecar.proxy.server import app

# ---------------------------------------------------------------------------
# Upstream base URLs
# ---------------------------------------------------------------------------

OPENAI_BASE = "https://api.openai.com"
ANTHROPIC_BASE = "https://api.anthropic.com"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    """Synchronous ASGI test client."""
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Response / chunk helpers – OpenAI
# ---------------------------------------------------------------------------


def _openai_chat_response(model: str = "gpt-4o-2024-05-13") -> dict[str, Any]:
    return {
        "id": "chatcmpl-proxy-test",
        "object": "chat.completion",
        "model": model,
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 25,
            "total_tokens": 37,
        },
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello from proxy!"},
                "finish_reason": "stop",
            }
        ],
    }


def _openai_sse_chunk(
    *,
    content: str | None = None,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
    model: str = "gpt-4o-2024-05-13",
) -> bytes:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content

    choice: dict[str, Any] = {"index": 0, "delta": delta}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason

    obj: dict[str, Any] = {
        "id": "chatcmpl-stream-proxy",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [choice],
    }
    if usage is not None:
        obj["usage"] = usage

    return f"data: {json.dumps(obj)}\n\n".encode()


_OPENAI_DONE = b"data: [DONE]\n\n"

_OPENAI_REQUEST: dict[str, Any] = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello proxy"}],
}

_OPENAI_STREAM_REQUEST: dict[str, Any] = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello proxy"}],
    "stream": True,
}


# ---------------------------------------------------------------------------
# Response / chunk helpers – Anthropic
# ---------------------------------------------------------------------------


def _anthropic_messages_response(model: str = "claude-3-opus-20240229") -> dict[str, Any]:
    return {
        "id": "msg_proxy_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": "Hello from proxy!"}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 12,
            "output_tokens": 25,
        },
    }


def _anthropic_sse_message_start(
    model: str = "claude-3-opus-20240229", input_tokens: int = 12
) -> bytes:
    obj: dict[str, Any] = {
        "type": "message_start",
        "message": {
            "id": "msg_stream_proxy",
            "type": "message",
            "role": "assistant",
            "model": model,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        },
    }
    return f"event: message_start\ndata: {json.dumps(obj)}\n\n".encode()


def _anthropic_sse_content_block_delta(text: str) -> bytes:
    obj: dict[str, Any] = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": text},
    }
    return f"event: content_block_delta\ndata: {json.dumps(obj)}\n\n".encode()


def _anthropic_sse_message_delta(
    stop_reason: str = "end_turn", output_tokens: int = 25
) -> bytes:
    obj: dict[str, Any] = {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    }
    return f"event: message_delta\ndata: {json.dumps(obj)}\n\n".encode()


def _anthropic_sse_message_stop() -> bytes:
    obj: dict[str, Any] = {"type": "message_stop"}
    return f"event: message_stop\ndata: {json.dumps(obj)}\n\n".encode()


_ANTHROPIC_REQUEST: dict[str, Any] = {
    "model": "claude-3-opus-20240229",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello proxy"}],
}

_ANTHROPIC_STREAM_REQUEST: dict[str, Any] = {
    "model": "claude-3-opus-20240229",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello proxy"}],
    "stream": True,
}


# ---------------------------------------------------------------------------
# 1. Health check
# ---------------------------------------------------------------------------


def test_health_check(client: TestClient) -> None:
    """GET /health must return {"status": "ok"}."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# 2. OpenAI non-streaming full flow
# ---------------------------------------------------------------------------


@respx.mock
def test_openai_non_streaming_upstream_url_called(client: TestClient) -> None:
    """Proxy must route to the correct OpenAI upstream URL."""
    route = respx.post(f"{OPENAI_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_chat_response())
    )

    resp = client.post(
        "/openai/v1/chat/completions",
        json=_OPENAI_REQUEST,
        headers={"Authorization": "Bearer sk-test"},
    )

    assert resp.status_code == 200
    assert route.called


@respx.mock
def test_openai_non_streaming_body_passthrough(client: TestClient) -> None:
    """OpenAI response body must arrive at the client unchanged."""
    expected = _openai_chat_response()
    respx.post(f"{OPENAI_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=expected)
    )

    resp = client.post("/openai/v1/chat/completions", json=_OPENAI_REQUEST)

    assert resp.status_code == 200
    assert resp.json() == expected


@respx.mock
def test_openai_non_streaming_authorization_forwarded(client: TestClient) -> None:
    """Authorization header must be forwarded to the OpenAI upstream."""
    captured: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_openai_chat_response())

    respx.post(f"{OPENAI_BASE}/v1/chat/completions").mock(side_effect=capture)

    client.post(
        "/openai/v1/chat/completions",
        json=_OPENAI_REQUEST,
        headers={"Authorization": "Bearer sk-forwarded"},
    )

    assert captured, "No upstream request was captured"
    assert captured[0].headers.get("authorization") == "Bearer sk-forwarded"


@respx.mock
def test_openai_non_streaming_span_attributes(client: TestClient) -> None:
    """Full-flow span must carry the correct provider, model, tokens, finish_reason."""
    respx.post(f"{OPENAI_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_chat_response())
    )

    with patch("llm_otel_sidecar.proxy.openai.emit_span") as mock_emit:
        resp = client.post("/openai/v1/chat/completions", json=_OPENAI_REQUEST)

    assert resp.status_code == 200
    mock_emit.assert_called_once()
    span = mock_emit.call_args[0][0]
    assert span.provider == "openai"
    assert span.model == "gpt-4o-2024-05-13"
    assert span.input_tokens == 12
    assert span.output_tokens == 25
    assert span.finish_reason == "stop"
    assert span.is_streaming is False
    assert span.error_type is None


# ---------------------------------------------------------------------------
# 3. Anthropic non-streaming full flow
# ---------------------------------------------------------------------------


@respx.mock
def test_anthropic_non_streaming_upstream_url_called(client: TestClient) -> None:
    """Proxy must route to the correct Anthropic upstream URL."""
    route = respx.post(f"{ANTHROPIC_BASE}/v1/messages").mock(
        return_value=httpx.Response(200, json=_anthropic_messages_response())
    )

    resp = client.post(
        "/anthropic/v1/messages",
        json=_ANTHROPIC_REQUEST,
        headers={"x-api-key": "sk-ant-test", "anthropic-version": "2023-06-01"},
    )

    assert resp.status_code == 200
    assert route.called


@respx.mock
def test_anthropic_non_streaming_body_passthrough(client: TestClient) -> None:
    """Anthropic response body must arrive at the client unchanged."""
    expected = _anthropic_messages_response()
    respx.post(f"{ANTHROPIC_BASE}/v1/messages").mock(
        return_value=httpx.Response(200, json=expected)
    )

    resp = client.post("/anthropic/v1/messages", json=_ANTHROPIC_REQUEST)

    assert resp.status_code == 200
    assert resp.json() == expected


@respx.mock
def test_anthropic_non_streaming_auth_headers_forwarded(client: TestClient) -> None:
    """x-api-key and anthropic-version headers must be forwarded to the Anthropic upstream."""
    captured: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_anthropic_messages_response())

    respx.post(f"{ANTHROPIC_BASE}/v1/messages").mock(side_effect=capture)

    client.post(
        "/anthropic/v1/messages",
        json=_ANTHROPIC_REQUEST,
        headers={"x-api-key": "sk-ant-forwarded", "anthropic-version": "2023-06-01"},
    )

    assert captured, "No upstream request was captured"
    upstream_req = captured[0]
    assert upstream_req.headers.get("x-api-key") == "sk-ant-forwarded"
    assert upstream_req.headers.get("anthropic-version") == "2023-06-01"


@respx.mock
def test_anthropic_non_streaming_span_attributes(client: TestClient) -> None:
    """Full-flow span must carry the correct provider, model, tokens, finish_reason."""
    respx.post(f"{ANTHROPIC_BASE}/v1/messages").mock(
        return_value=httpx.Response(200, json=_anthropic_messages_response())
    )

    with patch("llm_otel_sidecar.proxy.anthropic.emit_span") as mock_emit:
        resp = client.post("/anthropic/v1/messages", json=_ANTHROPIC_REQUEST)

    assert resp.status_code == 200
    mock_emit.assert_called_once()
    span = mock_emit.call_args[0][0]
    assert span.provider == "anthropic"
    assert span.model == "claude-3-opus-20240229"
    assert span.input_tokens == 12
    assert span.output_tokens == 25
    assert span.finish_reason == "end_turn"
    assert span.is_streaming is False
    assert span.error_type is None


# ---------------------------------------------------------------------------
# 4. Error passthrough (4xx forwarded unchanged)
# ---------------------------------------------------------------------------


@respx.mock
def test_openai_4xx_status_and_body_forwarded(client: TestClient) -> None:
    """A 4xx from the OpenAI upstream must be forwarded with the same status code and body."""
    error_body = {
        "error": {
            "message": "You exceeded your quota",
            "type": "insufficient_quota",
            "code": "rate_limit_exceeded",
        }
    }
    respx.post(f"{OPENAI_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(429, json=error_body)
    )

    resp = client.post("/openai/v1/chat/completions", json=_OPENAI_REQUEST)

    assert resp.status_code == 429
    assert resp.json() == error_body


@respx.mock
def test_anthropic_4xx_status_and_body_forwarded(client: TestClient) -> None:
    """A 4xx from the Anthropic upstream must be forwarded with the same status code and body."""
    error_body = {
        "type": "error",
        "error": {
            "type": "authentication_error",
            "message": "invalid x-api-key",
        },
    }
    respx.post(f"{ANTHROPIC_BASE}/v1/messages").mock(
        return_value=httpx.Response(401, json=error_body)
    )

    resp = client.post("/anthropic/v1/messages", json=_ANTHROPIC_REQUEST)

    assert resp.status_code == 401
    assert resp.json() == error_body


# ---------------------------------------------------------------------------
# 5. Streaming correctness – chunks arrive in order
# ---------------------------------------------------------------------------


@respx.mock
def test_openai_streaming_chunks_arrive_in_order(client: TestClient) -> None:
    """OpenAI SSE chunks must be forwarded to the client in the correct order."""
    chunk1 = _openai_sse_chunk(content="chunk-one")
    chunk2 = _openai_sse_chunk(content="chunk-two")
    chunk3 = _openai_sse_chunk(content="chunk-three", finish_reason="stop")
    usage_chunk = _openai_sse_chunk(
        usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
    )
    all_chunks = chunk1 + chunk2 + chunk3 + usage_chunk + _OPENAI_DONE

    respx.post(f"{OPENAI_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=all_chunks,
            headers={"content-type": "text/event-stream"},
        )
    )

    resp = client.post("/openai/v1/chat/completions", json=_OPENAI_STREAM_REQUEST)

    assert resp.status_code == 200
    body = resp.content
    # All three content chunks must be present and in order
    pos1 = body.index(b"chunk-one")
    pos2 = body.index(b"chunk-two")
    pos3 = body.index(b"chunk-three")
    assert pos1 < pos2 < pos3


@respx.mock
def test_anthropic_streaming_chunks_arrive_in_order(client: TestClient) -> None:
    """Anthropic SSE chunks must be forwarded to the client in the correct order."""
    chunks = (
        _anthropic_sse_message_start(input_tokens=12)
        + _anthropic_sse_content_block_delta("alpha")
        + _anthropic_sse_content_block_delta("beta")
        + _anthropic_sse_content_block_delta("gamma")
        + _anthropic_sse_message_delta(output_tokens=25)
        + _anthropic_sse_message_stop()
    )

    respx.post(f"{ANTHROPIC_BASE}/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=chunks,
            headers={"content-type": "text/event-stream"},
        )
    )

    resp = client.post("/anthropic/v1/messages", json=_ANTHROPIC_STREAM_REQUEST)

    assert resp.status_code == 200
    body = resp.content
    # All three text deltas must be present and in order
    pos_alpha = body.index(b"alpha")
    pos_beta = body.index(b"beta")
    pos_gamma = body.index(b"gamma")
    assert pos_alpha < pos_beta < pos_gamma
