from __future__ import annotations

"""Credential exposure tests.

Verifies that API keys and auth tokens passed through the proxy are never
leaked into OTel spans, response headers, or any other observable output.
"""

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from llm_otel_sidecar.proxy.server import app

OPENAI_UPSTREAM = "https://api.openai.com"
ANTHROPIC_UPSTREAM = "https://api.anthropic.com"

_OPENAI_TOKEN = "sk-real-secret-openai-token"
_ANTHROPIC_TOKEN = "sk-ant-real-secret-anthropic-token"

_CHAT_RESPONSE: dict[str, Any] = {
    "id": "chatcmpl-abc",
    "object": "chat.completion",
    "model": "gpt-4o-2024-05-13",
    "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
}

_ANTHROPIC_RESPONSE: dict[str, Any] = {
    "id": "msg-abc",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-sonnet-20241022",
    "content": [{"type": "text", "text": "Hi"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 5, "output_tokens": 10},
}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# 1. OpenAI — auth token must not appear in any span attribute or event
# ---------------------------------------------------------------------------


@respx.mock
def test_openai_auth_token_not_in_span_attributes(client: TestClient) -> None:
    """The OpenAI Authorization Bearer token must never appear in span attributes."""
    respx.post(f"{OPENAI_UPSTREAM}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_CHAT_RESPONSE)
    )

    with patch("llm_otel_sidecar.proxy.openai.emit_span") as mock_emit:
        client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
            headers={"Authorization": f"Bearer {_OPENAI_TOKEN}"},
        )

    mock_emit.assert_called_once()
    parsed = mock_emit.call_args[0][0]

    # Inspect every field of the ParsedSpan for the token
    for field_name, value in vars(parsed).items():
        assert _OPENAI_TOKEN not in str(value), (
            f"Auth token found in ParsedSpan field '{field_name}'"
        )


@respx.mock
def test_openai_auth_token_not_in_response_headers(client: TestClient) -> None:
    """The OpenAI Authorization header must not be echoed back to the client."""
    respx.post(f"{OPENAI_UPSTREAM}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_CHAT_RESPONSE)
    )

    resp = client.post(
        "/openai/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
        headers={"Authorization": f"Bearer {_OPENAI_TOKEN}"},
    )

    assert "authorization" not in {k.lower() for k in resp.headers}
    assert _OPENAI_TOKEN not in resp.text


# ---------------------------------------------------------------------------
# 2. Anthropic — x-api-key must not appear in any span attribute or response
# ---------------------------------------------------------------------------


@respx.mock
def test_anthropic_api_key_not_in_span_attributes(client: TestClient) -> None:
    """The Anthropic x-api-key must never appear in span attributes."""
    respx.post(f"{ANTHROPIC_UPSTREAM}/v1/messages").mock(
        return_value=httpx.Response(200, json=_ANTHROPIC_RESPONSE)
    )

    with patch("llm_otel_sidecar.proxy.anthropic.emit_span") as mock_emit:
        client.post(
            "/anthropic/v1/messages",
            json={"model": "claude-3-5-sonnet-20241022", "max_tokens": 10, "messages": [{"role": "user", "content": "Hi"}]},
            headers={"x-api-key": _ANTHROPIC_TOKEN, "anthropic-version": "2023-06-01"},
        )

    mock_emit.assert_called_once()
    parsed = mock_emit.call_args[0][0]

    for field_name, value in vars(parsed).items():
        assert _ANTHROPIC_TOKEN not in str(value), (
            f"API key found in ParsedSpan field '{field_name}'"
        )


@respx.mock
def test_anthropic_api_key_not_in_response_headers(client: TestClient) -> None:
    """The Anthropic x-api-key must not be echoed back to the client."""
    respx.post(f"{ANTHROPIC_UPSTREAM}/v1/messages").mock(
        return_value=httpx.Response(200, json=_ANTHROPIC_RESPONSE)
    )

    resp = client.post(
        "/anthropic/v1/messages",
        json={"model": "claude-3-5-sonnet-20241022", "max_tokens": 10, "messages": [{"role": "user", "content": "Hi"}]},
        headers={"x-api-key": _ANTHROPIC_TOKEN, "anthropic-version": "2023-06-01"},
    )

    assert "x-api-key" not in {k.lower() for k in resp.headers}
    assert _ANTHROPIC_TOKEN not in resp.text


# ---------------------------------------------------------------------------
# 3. CAPTURE_PROMPTS default is False — prompt content is private by default
# ---------------------------------------------------------------------------


@respx.mock
def test_prompts_not_in_span_by_default(client: TestClient) -> None:
    """With default config (CAPTURE_PROMPTS=False), prompt content must not reach the span."""
    secret_prompt = "My confidential system prompt with sensitive data"
    respx.post(f"{OPENAI_UPSTREAM}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_CHAT_RESPONSE)
    )

    with patch("llm_otel_sidecar.proxy.openai.emit_span") as mock_emit:
        client.post(
            "/openai/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": secret_prompt}],
            },
        )

    parsed = mock_emit.call_args[0][0]
    assert parsed.request_messages is None, (
        "Prompt content must not be captured when CAPTURE_PROMPTS is off"
    )

    for field_name, value in vars(parsed).items():
        assert secret_prompt not in str(value), (
            f"Prompt content found in ParsedSpan field '{field_name}'"
        )
