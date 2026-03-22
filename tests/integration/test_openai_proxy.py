from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from llm_otel_sidecar.proxy.server import app

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

UPSTREAM_BASE = "https://api.openai.com"


@pytest.fixture()
def client() -> TestClient:
    """Synchronous ASGI test client (BackgroundTasks run inline)."""
    return TestClient(app, raise_server_exceptions=True)


def _chat_response(model: str = "gpt-4o-2024-05-13") -> dict[str, Any]:
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "model": model,
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        },
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }
        ],
    }


def _sse_chunk(
    *,
    content: str | None = None,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
    model: str = "gpt-4o-2024-05-13",
    chunk_id: str = "chatcmpl-stream",
) -> bytes:
    """Build a single SSE data line."""
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content

    choice: dict[str, Any] = {"index": 0, "delta": delta}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason

    obj: dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [choice],
    }
    if usage is not None:
        obj["usage"] = usage

    return f"data: {json.dumps(obj)}\n\n".encode()


_DONE_CHUNK = b"data: [DONE]\n\n"

_STANDARD_REQUEST: dict[str, Any] = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hi"}],
}

_STREAMING_REQUEST: dict[str, Any] = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hi"}],
    "stream": True,
}


# ---------------------------------------------------------------------------
# 1. Non-streaming: upstream URL, body passthrough, span emitted
# ---------------------------------------------------------------------------


@respx.mock
def test_non_streaming_correct_upstream_url_called(client: TestClient) -> None:
    """Proxy must call the correct upstream URL."""
    route = respx.post(f"{UPSTREAM_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response())
    )

    resp = client.post(
        "/openai/v1/chat/completions",
        json=_STANDARD_REQUEST,
        headers={"Authorization": "Bearer sk-test"},
    )

    assert resp.status_code == 200
    assert route.called


@respx.mock
def test_non_streaming_body_passthrough_unchanged(client: TestClient) -> None:
    """Response body must be passed through to the client unchanged."""
    expected = _chat_response()
    respx.post(f"{UPSTREAM_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=expected)
    )

    resp = client.post("/openai/v1/chat/completions", json=_STANDARD_REQUEST)

    assert resp.status_code == 200
    assert resp.json() == expected


@respx.mock
def test_non_streaming_span_emitted(client: TestClient) -> None:
    """emit_span must be called once with correct attributes after non-streaming response."""
    respx.post(f"{UPSTREAM_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response())
    )

    with patch("llm_otel_sidecar.proxy.openai.emit_span") as mock_emit:
        resp = client.post("/openai/v1/chat/completions", json=_STANDARD_REQUEST)

    assert resp.status_code == 200
    mock_emit.assert_called_once()
    span = mock_emit.call_args[0][0]
    assert span.provider == "openai"
    assert span.model == "gpt-4o-2024-05-13"
    assert span.input_tokens == 10
    assert span.output_tokens == 20
    assert span.finish_reason == "stop"
    assert span.is_streaming is False
    assert span.error_type is None


# ---------------------------------------------------------------------------
# 2. Streaming: chunks forwarded, span emitted after stream ends
# ---------------------------------------------------------------------------


@respx.mock
def test_streaming_chunks_forwarded(client: TestClient) -> None:
    """All SSE chunks must be forwarded to the client in order."""
    chunk1 = _sse_chunk(content="Hello")
    chunk2 = _sse_chunk(content=" world", finish_reason="stop")
    chunk3 = _sse_chunk(
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    chunks = chunk1 + chunk2 + chunk3 + _DONE_CHUNK

    respx.post(f"{UPSTREAM_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=chunks,
            headers={"content-type": "text/event-stream"},
        )
    )

    resp = client.post("/openai/v1/chat/completions", json=_STREAMING_REQUEST)

    assert resp.status_code == 200
    assert b"Hello" in resp.content
    assert b" world" in resp.content


@respx.mock
def test_streaming_span_emitted_after_stream_ends(client: TestClient) -> None:
    """emit_span must be called once after the stream ends with streaming=True."""
    chunk1 = _sse_chunk(content="Hi", model="gpt-4o-2024-05-13")
    chunk2 = _sse_chunk(content="!", finish_reason="stop")
    chunk3 = _sse_chunk(
        usage={"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
    )
    chunks = chunk1 + chunk2 + chunk3 + _DONE_CHUNK

    respx.post(f"{UPSTREAM_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=chunks,
            headers={"content-type": "text/event-stream"},
        )
    )

    with patch("llm_otel_sidecar.proxy.openai.emit_span") as mock_emit:
        resp = client.post("/openai/v1/chat/completions", json=_STREAMING_REQUEST)

    assert resp.status_code == 200
    mock_emit.assert_called_once()
    span = mock_emit.call_args[0][0]
    assert span.is_streaming is True
    assert span.provider == "openai"
    assert span.model == "gpt-4o-2024-05-13"
    assert span.finish_reason == "stop"
    assert span.input_tokens == 8
    assert span.output_tokens == 3


# ---------------------------------------------------------------------------
# 3. stream_options.include_usage injected transparently
# ---------------------------------------------------------------------------


@respx.mock
def test_stream_options_include_usage_injected(client: TestClient) -> None:
    """include_usage must be injected even if stream_options is absent in request."""
    captured_body: list[bytes] = []

    def capture_and_respond(request: httpx.Request) -> httpx.Response:
        captured_body.append(request.content)
        return httpx.Response(
            200,
            content=_DONE_CHUNK,
            headers={"content-type": "text/event-stream"},
        )

    respx.post(f"{UPSTREAM_BASE}/v1/chat/completions").mock(side_effect=capture_and_respond)

    client.post("/openai/v1/chat/completions", json=_STREAMING_REQUEST)

    assert captured_body, "No request body was captured"
    sent = json.loads(captured_body[0])
    assert sent.get("stream_options", {}).get("include_usage") is True


@respx.mock
def test_stream_options_existing_keys_preserved(client: TestClient) -> None:
    """Existing stream_options keys must not be overwritten — only include_usage is added."""
    captured_body: list[bytes] = []

    def capture_and_respond(request: httpx.Request) -> httpx.Response:
        captured_body.append(request.content)
        return httpx.Response(
            200,
            content=_DONE_CHUNK,
            headers={"content-type": "text/event-stream"},
        )

    respx.post(f"{UPSTREAM_BASE}/v1/chat/completions").mock(side_effect=capture_and_respond)

    request_with_opts = dict(_STREAMING_REQUEST)
    request_with_opts["stream_options"] = {"some_other_key": "value"}
    client.post("/openai/v1/chat/completions", json=request_with_opts)

    assert captured_body
    sent = json.loads(captured_body[0])
    assert sent["stream_options"]["include_usage"] is True
    assert sent["stream_options"]["some_other_key"] == "value"


# ---------------------------------------------------------------------------
# 4. Upstream 4xx: response forwarded unchanged, span has error_type
# ---------------------------------------------------------------------------


@respx.mock
def test_upstream_4xx_forwarded_unchanged(client: TestClient) -> None:
    """A 4xx upstream response must be forwarded to the client with the same status/body."""
    error_body = {
        "error": {
            "message": "You exceeded your quota",
            "type": "insufficient_quota",
            "code": "rate_limit_exceeded",
        }
    }
    respx.post(f"{UPSTREAM_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(429, json=error_body)
    )

    resp = client.post("/openai/v1/chat/completions", json=_STANDARD_REQUEST)

    assert resp.status_code == 429
    assert resp.json() == error_body


@respx.mock
def test_upstream_4xx_span_has_error_type(client: TestClient) -> None:
    """On 4xx, the emitted span must have error_type populated."""
    error_body = {
        "error": {
            "message": "Not found",
            "type": "model_not_found",
        }
    }
    respx.post(f"{UPSTREAM_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(404, json=error_body)
    )

    with patch("llm_otel_sidecar.proxy.openai.emit_span") as mock_emit:
        resp = client.post("/openai/v1/chat/completions", json=_STANDARD_REQUEST)

    assert resp.status_code == 404
    mock_emit.assert_called_once()
    span = mock_emit.call_args[0][0]
    assert span.error_type == "model_not_found"
    assert span.status_code == 404


# ---------------------------------------------------------------------------
# 5. Upstream timeout: returns 504
# ---------------------------------------------------------------------------


@respx.mock
def test_upstream_timeout_returns_504(client: TestClient) -> None:
    """On an upstream TimeoutException the proxy must return 504."""
    respx.post(f"{UPSTREAM_BASE}/v1/chat/completions").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    resp = client.post("/openai/v1/chat/completions", json=_STANDARD_REQUEST)

    assert resp.status_code == 504
    assert resp.content == b"upstream timeout"
