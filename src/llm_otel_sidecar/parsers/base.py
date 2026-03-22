from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ParsedSpan:
    """Contract between parsers and the OTel emitter.

    Neither side knows about the other's implementation — parsers return
    ParsedSpan, the emitter accepts ParsedSpan.
    """

    provider: str  # "openai" | "anthropic"
    request_model: str  # model sent in the request, e.g. "gpt-4o"
    response_model: str  # model reported in the response (may differ from request)
    latency_ms: float
    status_code: int
    is_streaming: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    error_type: str | None = None  # set on non-2xx responses
    request_messages: list[dict[str, Any]] | None = None  # captured for span events (optional)
