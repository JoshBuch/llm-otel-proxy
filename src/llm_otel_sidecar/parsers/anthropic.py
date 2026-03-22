from __future__ import annotations

from typing import Any

from llm_otel_sidecar.config import config
from llm_otel_sidecar.parsers.base import ParsedSpan
from llm_otel_sidecar.telemetry.conventions import UNKNOWN_MODEL

PROVIDER = "anthropic"


def parse_anthropic_response(
    request_body: dict[str, Any],
    response_body: dict[str, Any],
    status_code: int,
    latency_ms: float,
    is_streaming: bool,
) -> ParsedSpan:
    """Parse an Anthropic response into a ParsedSpan.

    Works for both non-streaming responses and accumulated/merged streaming
    response dicts (i.e. the dict reconstructed after SSE events complete).
    For streaming, usage is expected in response_body["usage"] and stop_reason
    may appear at response_body["stop_reason"] or response_body["delta"]["stop_reason"].
    """
    # --- model -----------------------------------------------------------
    request_model: str = request_body.get("model") or UNKNOWN_MODEL
    response_model: str = response_body.get("model") or request_body.get("model") or UNKNOWN_MODEL

    # --- token usage -----------------------------------------------------
    usage: dict[str, Any] | None = response_body.get("usage")
    input_tokens: int | None = None
    output_tokens: int | None = None
    if usage is not None:
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")

    # --- finish_reason ---------------------------------------------------
    # Prefer top-level stop_reason; fall back to delta.stop_reason for streaming
    finish_reason: str | None = response_body.get("stop_reason")
    if finish_reason is None:
        delta: dict[str, Any] = response_body.get("delta") or {}
        finish_reason = delta.get("stop_reason")

    # --- error_type (only on non-2xx) ------------------------------------
    error_type: str | None = None
    if status_code < 200 or status_code >= 300:
        error_obj: dict[str, Any] = response_body.get("error") or {}
        error_type = error_obj.get("type")

    # --- request_messages (gated by CAPTURE_PROMPTS) ---------------------
    request_messages: list[dict[str, Any]] | None = None
    if config.capture_prompts:
        request_messages = request_body.get("messages")

    return ParsedSpan(
        provider=PROVIDER,
        request_model=request_model,
        response_model=response_model,
        latency_ms=latency_ms,
        status_code=status_code,
        is_streaming=is_streaming,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        finish_reason=finish_reason,
        error_type=error_type,
        request_messages=request_messages,
    )
