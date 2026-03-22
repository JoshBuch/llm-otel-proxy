from __future__ import annotations

from typing import Any

from llm_otel_sidecar.config import config
from llm_otel_sidecar.parsers.base import ParsedSpan

PROVIDER = "openai"


def parse_openai_response(
    request_body: dict[str, Any],
    response_body: dict[str, Any],
    status_code: int,
    latency_ms: float,
    is_streaming: bool,
) -> ParsedSpan:
    """Parse an OpenAI (or OpenAI-compatible) response into a ParsedSpan.

    Works for both non-streaming responses and accumulated/merged streaming
    response dicts (i.e. the dict reconstructed after the [DONE] SSE event).
    """
    # --- model -----------------------------------------------------------
    model: str = response_body.get("model") or request_body.get("model") or "unknown"

    # --- token usage -----------------------------------------------------
    usage: dict[str, Any] | None = response_body.get("usage")
    input_tokens: int | None = None
    output_tokens: int | None = None
    if usage is not None:
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")

    # --- finish_reason ---------------------------------------------------
    choices: list[Any] = response_body.get("choices") or []
    finish_reason: str | None = choices[0].get("finish_reason") if choices else None

    # --- error_type (only on non-2xx) ------------------------------------
    error_type: str | None = None
    if status_code < 200 or status_code >= 300:
        error_obj: dict[str, Any] = response_body.get("error") or {}
        error_type = error_obj.get("type") or error_obj.get("code")

    # --- request_messages (gated by CAPTURE_PROMPTS) ---------------------
    request_messages: list[dict[str, Any]] | None = None
    if config.capture_prompts:
        request_messages = request_body.get("messages")

    return ParsedSpan(
        provider=PROVIDER,
        model=model,
        latency_ms=latency_ms,
        status_code=status_code,
        is_streaming=is_streaming,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        finish_reason=finish_reason,
        error_type=error_type,
        request_messages=request_messages,
    )
