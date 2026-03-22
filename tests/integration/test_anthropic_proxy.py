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
# Fixtures / helpers
# ---------------------------------------------------------------------------

UPSTREAM_BASE = "https://api.anthropic.com"


@pytest.fixture()
def client() -> TestClient:
    """Synchronous ASGI test client (BackgroundTasks run inline)."""
    return TestClient(app, raise_server_exceptions=True)


def _messages_response(model: str = "claude-3-opus-20240229") -> dict[str, Any]:
    return {
        "id": "msg_abc123",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": "Hello!"}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
        },
    }


def _sse_message_start(model: str = "claude-3-opus-20240229", input_tokens: int = 10) -> bytes:
    obj: dict[str, Any] = {
        "type": "message_start",
        "message": {
            "id": "msg_stream",
            "type": "message",
            "role": "assistant",
            "model": model,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        },
    }
    return f"event: message_start\ndata: {json.dumps(obj)}\n\n".encode()


def _sse_content_block_delta(text: str) -> bytes:
    obj: dict[str, Any] = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": text},
    }
    return f"event: content_block_delta\ndata: {json.dumps(obj)}\n\n".encode()


def _sse_message_delta(stop_reason: str = "end_turn", output_tokens: int = 15) -> bytes:
    obj: dict[str, Any] = {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    }
    return f"event: message_delta\ndata: {json.dumps(obj)}\n\n".encode()


def _sse_message_stop() -> bytes:
    obj: dict[str, Any] = {"type": "message_stop"}
    return f"event: message_stop\ndata: {json.dumps(obj)}\n\n".encode()


_STANDARD_REQUEST: dict[str, Any] = {
    "model": "claude-3-opus-20240229",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hi"}],
}

_STREAMING_REQUEST: dict[str, Any] = {
    "model": "claude-3-opus-20240229",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hi"}],
    "stream": True,
}


# ---------------------------------------------------------------------------
# 1. Non-streaming: correct upstream URL, response passthrough, span emitted
# ---------------------------------------------------------------------------


@respx.mock
def test_non_streaming_correct_upstream_url_called(client: TestClient) -> None:
    """Proxy must call the correct upstream URL."""
    route = respx.post(f"{UPSTREAM_BASE}/v1/messages").mock(
        return_value=httpx.Response(200, json=_messages_response())
    )

    resp = client.post(
        "/anthropic/v1/messages",
        json=_STANDARD_REQUEST,
        headers={"x-api-key": "sk-ant-test", "anthropic-version": "2023-06-01"},
    )

    assert resp.status_code == 200
    assert route.called


@respx.mock
def test_non_streaming_body_passthrough_unchanged(client: TestClient) -> None:
    """Response body must be passed through to the client unchanged."""
    expected = _messages_response()
    respx.post(f"{UPSTREAM_BASE}/v1/messages").mock(
        return_value=httpx.Response(200, json=expected)
    )

    resp = client.post("/anthropic/v1/messages", json=_STANDARD_REQUEST)

    assert resp.status_code == 200
    assert resp.json() == expected


@respx.mock
def test_non_streaming_span_emitted(client: TestClient) -> None:
    """emit_span must be called once with correct attributes after non-streaming response."""
    respx.post(f"{UPSTREAM_BASE}/v1/messages").mock(
        return_value=httpx.Response(200, json=_messages_response())
    )

    with patch("llm_otel_sidecar.proxy.anthropic.emit_span") as mock_emit:
        resp = client.post("/anthropic/v1/messages", json=_STANDARD_REQUEST)

    assert resp.status_code == 200
    mock_emit.assert_called_once()
    span = mock_emit.call_args[0][0]
    assert span.provider == "anthropic"
    assert span.request_model == "claude-3-opus-20240229"
    assert span.response_model == "claude-3-opus-20240229"
    assert span.input_tokens == 10
    assert span.output_tokens == 20
    assert span.finish_reason == "end_turn"
    assert span.is_streaming is False
    assert span.error_type is None


# ---------------------------------------------------------------------------
# 2. Streaming: chunks forwarded, span emitted after stream
# ---------------------------------------------------------------------------


@respx.mock
def test_streaming_chunks_forwarded(client: TestClient) -> None:
    """All SSE chunks must be forwarded to the client in order."""
    chunks = (
        _sse_message_start()
        + _sse_content_block_delta("Hello")
        + _sse_content_block_delta(" world")
        + _sse_message_delta()
        + _sse_message_stop()
    )

    respx.post(f"{UPSTREAM_BASE}/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=chunks,
            headers={"content-type": "text/event-stream"},
        )
    )

    resp = client.post("/anthropic/v1/messages", json=_STREAMING_REQUEST)

    assert resp.status_code == 200
    assert b"Hello" in resp.content
    assert b" world" in resp.content


@respx.mock
def test_streaming_span_emitted_after_stream_ends(client: TestClient) -> None:
    """emit_span must be called once after the stream ends with streaming=True."""
    chunks = (
        _sse_message_start(model="claude-3-opus-20240229", input_tokens=8)
        + _sse_content_block_delta("Hi!")
        + _sse_message_delta(stop_reason="end_turn", output_tokens=3)
        + _sse_message_stop()
    )

    respx.post(f"{UPSTREAM_BASE}/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=chunks,
            headers={"content-type": "text/event-stream"},
        )
    )

    with patch("llm_otel_sidecar.proxy.anthropic.emit_span") as mock_emit:
        resp = client.post("/anthropic/v1/messages", json=_STREAMING_REQUEST)

    assert resp.status_code == 200
    mock_emit.assert_called_once()
    span = mock_emit.call_args[0][0]
    assert span.is_streaming is True
    assert span.provider == "anthropic"
    assert span.request_model == "claude-3-opus-20240229"
    assert span.response_model == "claude-3-opus-20240229"
    assert span.finish_reason == "end_turn"
    assert span.input_tokens == 8
    assert span.output_tokens == 3


# ---------------------------------------------------------------------------
# 3. Upstream 4xx: forwarded unchanged, span has error_type
# ---------------------------------------------------------------------------


@respx.mock
def test_upstream_4xx_forwarded_unchanged(client: TestClient) -> None:
    """A 4xx upstream response must be forwarded to the client with the same status/body."""
    error_body = {
        "type": "error",
        "error": {
            "type": "authentication_error",
            "message": "invalid x-api-key",
        },
    }
    respx.post(f"{UPSTREAM_BASE}/v1/messages").mock(
        return_value=httpx.Response(401, json=error_body)
    )

    resp = client.post("/anthropic/v1/messages", json=_STANDARD_REQUEST)

    assert resp.status_code == 401
    assert resp.json() == error_body


@respx.mock
def test_upstream_4xx_span_has_error_type(client: TestClient) -> None:
    """On 4xx, the emitted span must have error_type populated."""
    error_body = {
        "type": "error",
        "error": {
            "type": "not_found_error",
            "message": "model not found",
        },
    }
    respx.post(f"{UPSTREAM_BASE}/v1/messages").mock(
        return_value=httpx.Response(404, json=error_body)
    )

    with patch("llm_otel_sidecar.proxy.anthropic.emit_span") as mock_emit:
        resp = client.post("/anthropic/v1/messages", json=_STANDARD_REQUEST)

    assert resp.status_code == 404
    mock_emit.assert_called_once()
    span = mock_emit.call_args[0][0]
    assert span.error_type == "not_found_error"
    assert span.status_code == 404


# ---------------------------------------------------------------------------
# 4. Upstream timeout: generator ends cleanly, error span emitted
# ---------------------------------------------------------------------------


@respx.mock
def test_upstream_timeout_non_streaming_returns_504(client: TestClient) -> None:
    """On an upstream TimeoutException the proxy must return 504 for non-streaming."""
    respx.post(f"{UPSTREAM_BASE}/v1/messages").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    resp = client.post("/anthropic/v1/messages", json=_STANDARD_REQUEST)

    assert resp.status_code == 504
    assert resp.content == b"upstream timeout"


@respx.mock
def test_upstream_timeout_streaming_emits_error_span(client: TestClient) -> None:
    """On a streaming timeout the generator ends cleanly and emits a timeout error span."""
    respx.post(f"{UPSTREAM_BASE}/v1/messages").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    with patch("llm_otel_sidecar.proxy.anthropic.emit_span") as mock_emit:
        resp = client.post("/anthropic/v1/messages", json=_STREAMING_REQUEST)

    # Stream should end without raising; status reflects the upstream timeout
    assert resp.status_code == 504
    mock_emit.assert_called_once()
    span = mock_emit.call_args[0][0]
    assert span.status_code == 504
    assert span.error_type == "timeout"
    assert span.is_streaming is True
