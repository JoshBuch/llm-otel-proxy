from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedSpan:
    """Contract between parsers and the OTel emitter.

    Neither side knows about the other's implementation — parsers return
    ParsedSpan, the emitter accepts ParsedSpan.
    """

    provider: str  # "openai" | "anthropic"
    model: str  # e.g. "gpt-4o"
    input_tokens: int | None
    output_tokens: int | None
    finish_reason: str | None
    latency_ms: float
    status_code: int
    error_type: str | None  # set on non-2xx responses
    is_streaming: bool
    request_messages: list[dict] | None  # captured for span events (optional)
