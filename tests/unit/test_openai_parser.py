from __future__ import annotations

from typing import Any
from unittest.mock import patch

from llm_otel_sidecar.parsers.openai import parse_openai_response


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

STANDARD_REQUEST: dict[str, Any] = {
    "model": "gpt-4o",
    "messages": [
        {"role": "user", "content": "Hello!"},
    ],
}

STANDARD_RESPONSE: dict[str, Any] = {
    "id": "chatcmpl-abc123",
    "object": "chat.completion",
    "model": "gpt-4o-2024-05-13",
    "usage": {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
    },
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hi there!"},
            "finish_reason": "stop",
        }
    ],
}


# ---------------------------------------------------------------------------
# Test 1: Non-streaming response — all fields extracted correctly
# ---------------------------------------------------------------------------


def test_non_streaming_all_fields():
    span = parse_openai_response(
        request_body=STANDARD_REQUEST,
        response_body=STANDARD_RESPONSE,
        status_code=200,
        latency_ms=123.4,
        is_streaming=False,
    )

    assert span.provider == "openai"
    assert span.model == "gpt-4o-2024-05-13"
    assert span.latency_ms == 123.4
    assert span.status_code == 200
    assert span.is_streaming is False
    assert span.input_tokens == 10
    assert span.output_tokens == 20
    assert span.finish_reason == "stop"
    assert span.error_type is None


# ---------------------------------------------------------------------------
# Test 2: Streaming response — same extraction from merged response dict
# ---------------------------------------------------------------------------


def test_streaming_same_extraction():
    streaming_response: dict[str, Any] = {
        "id": "chatcmpl-stream123",
        "object": "chat.completion.chunk",
        "model": "gpt-4o-2024-05-13",
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 25,
            "total_tokens": 40,
        },
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }
        ],
    }

    span = parse_openai_response(
        request_body=STANDARD_REQUEST,
        response_body=streaming_response,
        status_code=200,
        latency_ms=50.0,
        is_streaming=True,
    )

    assert span.provider == "openai"
    assert span.model == "gpt-4o-2024-05-13"
    assert span.is_streaming is True
    assert span.input_tokens == 15
    assert span.output_tokens == 25
    assert span.finish_reason == "stop"
    assert span.error_type is None


# ---------------------------------------------------------------------------
# Test 3: usage is None → input_tokens/output_tokens are None
# ---------------------------------------------------------------------------


def test_missing_usage_returns_none_tokens():
    response_no_usage: dict[str, Any] = {
        "id": "chatcmpl-abc",
        "model": "gpt-4o",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}
        ],
        # "usage" key intentionally absent
    }

    span = parse_openai_response(
        request_body=STANDARD_REQUEST,
        response_body=response_no_usage,
        status_code=200,
        latency_ms=10.0,
        is_streaming=False,
    )

    assert span.input_tokens is None
    assert span.output_tokens is None


def test_usage_none_value_returns_none_tokens():
    response_usage_none: dict[str, Any] = {
        "id": "chatcmpl-abc",
        "model": "gpt-4o",
        "usage": None,
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}
        ],
    }

    span = parse_openai_response(
        request_body=STANDARD_REQUEST,
        response_body=response_usage_none,
        status_code=200,
        latency_ms=10.0,
        is_streaming=False,
    )

    assert span.input_tokens is None
    assert span.output_tokens is None


# ---------------------------------------------------------------------------
# Test 4: choices is empty/absent → finish_reason is None
# ---------------------------------------------------------------------------


def test_empty_choices_finish_reason_none():
    response_empty_choices: dict[str, Any] = {
        "id": "chatcmpl-abc",
        "model": "gpt-4o",
        "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        "choices": [],
    }

    span = parse_openai_response(
        request_body=STANDARD_REQUEST,
        response_body=response_empty_choices,
        status_code=200,
        latency_ms=5.0,
        is_streaming=False,
    )

    assert span.finish_reason is None


def test_absent_choices_finish_reason_none():
    response_no_choices: dict[str, Any] = {
        "id": "chatcmpl-abc",
        "model": "gpt-4o",
        "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        # "choices" key intentionally absent
    }

    span = parse_openai_response(
        request_body=STANDARD_REQUEST,
        response_body=response_no_choices,
        status_code=200,
        latency_ms=5.0,
        is_streaming=False,
    )

    assert span.finish_reason is None


# ---------------------------------------------------------------------------
# Test 5: model absent in response → falls back to request model
# ---------------------------------------------------------------------------


def test_model_absent_in_response_falls_back_to_request():
    response_no_model: dict[str, Any] = {
        "id": "chatcmpl-abc",
        "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}
        ],
        # "model" key intentionally absent
    }

    span = parse_openai_response(
        request_body=STANDARD_REQUEST,
        response_body=response_no_model,
        status_code=200,
        latency_ms=5.0,
        is_streaming=False,
    )

    assert span.model == "gpt-4o"


def test_model_absent_everywhere_returns_unknown():
    response_no_model: dict[str, Any] = {
        "id": "chatcmpl-abc",
        "choices": [],
    }
    request_no_model: dict[str, Any] = {
        "messages": [{"role": "user", "content": "hi"}],
        # "model" key intentionally absent
    }

    span = parse_openai_response(
        request_body=request_no_model,
        response_body=response_no_model,
        status_code=200,
        latency_ms=5.0,
        is_streaming=False,
    )

    assert span.model == "unknown"


# ---------------------------------------------------------------------------
# Test 6: Non-2xx response → error_type is populated
# ---------------------------------------------------------------------------


def test_non_2xx_error_type_from_type_field():
    error_response: dict[str, Any] = {
        "error": {
            "message": "You exceeded your current quota",
            "type": "insufficient_quota",
            "code": "rate_limit_exceeded",
        }
    }

    span = parse_openai_response(
        request_body=STANDARD_REQUEST,
        response_body=error_response,
        status_code=429,
        latency_ms=20.0,
        is_streaming=False,
    )

    assert span.status_code == 429
    assert span.error_type == "insufficient_quota"


def test_non_2xx_error_type_falls_back_to_code():
    error_response: dict[str, Any] = {
        "error": {
            "message": "Not found",
            "code": "model_not_found",
            # "type" key intentionally absent
        }
    }

    span = parse_openai_response(
        request_body=STANDARD_REQUEST,
        response_body=error_response,
        status_code=404,
        latency_ms=5.0,
        is_streaming=False,
    )

    assert span.error_type == "model_not_found"


def test_non_2xx_no_error_body_error_type_none():
    span = parse_openai_response(
        request_body=STANDARD_REQUEST,
        response_body={},
        status_code=500,
        latency_ms=1.0,
        is_streaming=False,
    )

    assert span.status_code == 500
    assert span.error_type is None


def test_2xx_error_type_is_none():
    span = parse_openai_response(
        request_body=STANDARD_REQUEST,
        response_body=STANDARD_RESPONSE,
        status_code=200,
        latency_ms=100.0,
        is_streaming=False,
    )

    assert span.error_type is None


# ---------------------------------------------------------------------------
# Test 7: CAPTURE_PROMPTS toggles request_messages
# ---------------------------------------------------------------------------


def test_capture_prompts_false_request_messages_none():
    """When CAPTURE_PROMPTS is False, request_messages must be None."""
    with patch("llm_otel_sidecar.parsers.openai.config") as mock_config:
        mock_config.capture_prompts = False
        span = parse_openai_response(
            request_body=STANDARD_REQUEST,
            response_body=STANDARD_RESPONSE,
            status_code=200,
            latency_ms=50.0,
            is_streaming=False,
        )

    assert span.request_messages is None


def test_capture_prompts_true_request_messages_populated():
    """When CAPTURE_PROMPTS is True, request_messages must be the messages list."""
    with patch("llm_otel_sidecar.parsers.openai.config") as mock_config:
        mock_config.capture_prompts = True
        span = parse_openai_response(
            request_body=STANDARD_REQUEST,
            response_body=STANDARD_RESPONSE,
            status_code=200,
            latency_ms=50.0,
            is_streaming=False,
        )

    assert span.request_messages == [{"role": "user", "content": "Hello!"}]


def test_capture_prompts_true_missing_messages_returns_none():
    """When CAPTURE_PROMPTS is True but request has no messages key, return None."""
    request_no_messages: dict[str, Any] = {"model": "gpt-4o"}

    with patch("llm_otel_sidecar.parsers.openai.config") as mock_config:
        mock_config.capture_prompts = True
        span = parse_openai_response(
            request_body=request_no_messages,
            response_body=STANDARD_RESPONSE,
            status_code=200,
            latency_ms=50.0,
            is_streaming=False,
        )

    assert span.request_messages is None
