from __future__ import annotations

from typing import Any
from unittest.mock import patch

from llm_otel_sidecar.parsers.anthropic import parse_anthropic_response


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

STANDARD_REQUEST: dict[str, Any] = {
    "model": "claude-3-5-sonnet-20241022",
    "messages": [
        {"role": "user", "content": "Hello!"},
    ],
}

STANDARD_RESPONSE: dict[str, Any] = {
    "id": "msg_abc123",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-sonnet-20241022",
    "content": [{"type": "text", "text": "Hi there!"}],
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 10,
        "output_tokens": 20,
    },
}


# ---------------------------------------------------------------------------
# Test 1: Non-streaming response — all fields extracted correctly
# ---------------------------------------------------------------------------


def test_non_streaming_all_fields():
    span = parse_anthropic_response(
        request_body=STANDARD_REQUEST,
        response_body=STANDARD_RESPONSE,
        status_code=200,
        latency_ms=123.4,
        is_streaming=False,
    )

    assert span.provider == "anthropic"
    assert span.request_model == "claude-3-5-sonnet-20241022"
    assert span.response_model == "claude-3-5-sonnet-20241022"
    assert span.latency_ms == 123.4
    assert span.status_code == 200
    assert span.is_streaming is False
    assert span.input_tokens == 10
    assert span.output_tokens == 20
    assert span.finish_reason == "end_turn"
    assert span.error_type is None


# ---------------------------------------------------------------------------
# Test 2: Streaming — stop_reason in delta.stop_reason path
# ---------------------------------------------------------------------------


def test_streaming_stop_reason_from_delta():
    """For streaming, stop_reason may come from response_body['delta']['stop_reason']."""
    streaming_response: dict[str, Any] = {
        "type": "message_delta",
        "model": "claude-3-5-sonnet-20241022",
        "delta": {
            "stop_reason": "end_turn",
            "stop_sequence": None,
        },
        "usage": {
            "input_tokens": 15,
            "output_tokens": 25,
        },
    }

    span = parse_anthropic_response(
        request_body=STANDARD_REQUEST,
        response_body=streaming_response,
        status_code=200,
        latency_ms=50.0,
        is_streaming=True,
    )

    assert span.provider == "anthropic"
    assert span.request_model == "claude-3-5-sonnet-20241022"
    assert span.response_model == "claude-3-5-sonnet-20241022"
    assert span.is_streaming is True
    assert span.input_tokens == 15
    assert span.output_tokens == 25
    assert span.finish_reason == "end_turn"
    assert span.error_type is None


def test_streaming_stop_reason_top_level_preferred():
    """Top-level stop_reason takes precedence over delta.stop_reason."""
    streaming_response: dict[str, Any] = {
        "type": "message_delta",
        "model": "claude-3-5-sonnet-20241022",
        "stop_reason": "max_tokens",
        "delta": {
            "stop_reason": "end_turn",
        },
        "usage": {
            "input_tokens": 15,
            "output_tokens": 25,
        },
    }

    span = parse_anthropic_response(
        request_body=STANDARD_REQUEST,
        response_body=streaming_response,
        status_code=200,
        latency_ms=50.0,
        is_streaming=True,
    )

    assert span.finish_reason == "max_tokens"


# ---------------------------------------------------------------------------
# Test 3: usage is None → input_tokens/output_tokens are None
# ---------------------------------------------------------------------------


def test_missing_usage_returns_none_tokens():
    response_no_usage: dict[str, Any] = {
        "id": "msg_abc",
        "model": "claude-3-5-sonnet-20241022",
        "stop_reason": "end_turn",
        # "usage" key intentionally absent
    }

    span = parse_anthropic_response(
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
        "id": "msg_abc",
        "model": "claude-3-5-sonnet-20241022",
        "usage": None,
        "stop_reason": "end_turn",
    }

    span = parse_anthropic_response(
        request_body=STANDARD_REQUEST,
        response_body=response_usage_none,
        status_code=200,
        latency_ms=10.0,
        is_streaming=False,
    )

    assert span.input_tokens is None
    assert span.output_tokens is None


# ---------------------------------------------------------------------------
# Test 4: stop_reason absent → finish_reason is None
# ---------------------------------------------------------------------------


def test_stop_reason_absent_finish_reason_none():
    response_no_stop: dict[str, Any] = {
        "id": "msg_abc",
        "model": "claude-3-5-sonnet-20241022",
        "usage": {"input_tokens": 5, "output_tokens": 0},
        # "stop_reason" key intentionally absent
    }

    span = parse_anthropic_response(
        request_body=STANDARD_REQUEST,
        response_body=response_no_stop,
        status_code=200,
        latency_ms=5.0,
        is_streaming=False,
    )

    assert span.finish_reason is None


def test_streaming_stop_reason_absent_everywhere_finish_reason_none():
    """No stop_reason at top-level or in delta → finish_reason is None."""
    streaming_response: dict[str, Any] = {
        "type": "message_delta",
        "model": "claude-3-5-sonnet-20241022",
        "delta": {},
        "usage": {"input_tokens": 5, "output_tokens": 10},
    }

    span = parse_anthropic_response(
        request_body=STANDARD_REQUEST,
        response_body=streaming_response,
        status_code=200,
        latency_ms=5.0,
        is_streaming=True,
    )

    assert span.finish_reason is None


# ---------------------------------------------------------------------------
# Test 5: model absent in response → falls back to request model
# ---------------------------------------------------------------------------


def test_model_absent_in_response_falls_back_to_request():
    response_no_model: dict[str, Any] = {
        "id": "msg_abc",
        "usage": {"input_tokens": 5, "output_tokens": 10},
        "stop_reason": "end_turn",
        # "model" key intentionally absent
    }

    span = parse_anthropic_response(
        request_body=STANDARD_REQUEST,
        response_body=response_no_model,
        status_code=200,
        latency_ms=5.0,
        is_streaming=False,
    )

    assert span.response_model == "claude-3-5-sonnet-20241022"


def test_model_absent_everywhere_returns_unknown():
    response_no_model: dict[str, Any] = {
        "id": "msg_abc",
    }
    request_no_model: dict[str, Any] = {
        "messages": [{"role": "user", "content": "hi"}],
        # "model" key intentionally absent
    }

    span = parse_anthropic_response(
        request_body=request_no_model,
        response_body=response_no_model,
        status_code=200,
        latency_ms=5.0,
        is_streaming=False,
    )

    assert span.request_model == "unknown"
    assert span.response_model == "unknown"


# ---------------------------------------------------------------------------
# Test 6: Non-2xx response → error_type is populated
# ---------------------------------------------------------------------------


def test_non_2xx_error_type_populated():
    error_response: dict[str, Any] = {
        "type": "error",
        "error": {
            "type": "rate_limit_error",
            "message": "Too many requests.",
        },
    }

    span = parse_anthropic_response(
        request_body=STANDARD_REQUEST,
        response_body=error_response,
        status_code=429,
        latency_ms=20.0,
        is_streaming=False,
    )

    assert span.status_code == 429
    assert span.error_type == "rate_limit_error"


def test_non_2xx_no_error_body_error_type_none():
    span = parse_anthropic_response(
        request_body=STANDARD_REQUEST,
        response_body={},
        status_code=500,
        latency_ms=1.0,
        is_streaming=False,
    )

    assert span.status_code == 500
    assert span.error_type is None


def test_2xx_error_type_is_none():
    span = parse_anthropic_response(
        request_body=STANDARD_REQUEST,
        response_body=STANDARD_RESPONSE,
        status_code=200,
        latency_ms=100.0,
        is_streaming=False,
    )

    assert span.error_type is None


def test_status_199_is_treated_as_error() -> None:
    span = parse_anthropic_response(
        request_body={"model": "claude-3-opus-20240229", "messages": []},
        response_body={"error": {"type": "invalid_request_error"}},
        status_code=199,
        latency_ms=50.0,
        is_streaming=False,
    )
    assert span.error_type == "invalid_request_error"


# ---------------------------------------------------------------------------
# Test 7: CAPTURE_PROMPTS toggles request_messages
# ---------------------------------------------------------------------------


def test_capture_prompts_false_request_messages_none():
    """When CAPTURE_PROMPTS is False, request_messages must be None."""
    with patch("llm_otel_sidecar.parsers.anthropic.config") as mock_config:
        mock_config.capture_prompts = False
        span = parse_anthropic_response(
            request_body=STANDARD_REQUEST,
            response_body=STANDARD_RESPONSE,
            status_code=200,
            latency_ms=50.0,
            is_streaming=False,
        )

    assert span.request_messages is None


def test_capture_prompts_true_request_messages_populated():
    """When CAPTURE_PROMPTS is True, request_messages must be the messages list."""
    with patch("llm_otel_sidecar.parsers.anthropic.config") as mock_config:
        mock_config.capture_prompts = True
        span = parse_anthropic_response(
            request_body=STANDARD_REQUEST,
            response_body=STANDARD_RESPONSE,
            status_code=200,
            latency_ms=50.0,
            is_streaming=False,
        )

    assert span.request_messages == [{"role": "user", "content": "Hello!"}]


def test_capture_prompts_true_missing_messages_returns_none():
    """When CAPTURE_PROMPTS is True but request has no messages key, return None."""
    request_no_messages: dict[str, Any] = {"model": "claude-3-5-sonnet-20241022"}

    with patch("llm_otel_sidecar.parsers.anthropic.config") as mock_config:
        mock_config.capture_prompts = True
        span = parse_anthropic_response(
            request_body=request_no_messages,
            response_body=STANDARD_RESPONSE,
            status_code=200,
            latency_ms=50.0,
            is_streaming=False,
        )

    assert span.request_messages is None
