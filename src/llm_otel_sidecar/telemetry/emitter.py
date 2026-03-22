from __future__ import annotations

import json
import logging
from typing import Final

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer

from llm_otel_sidecar.parsers.base import ParsedSpan
from llm_otel_sidecar.telemetry.conventions import (
    ERROR_TYPE,
    GEN_AI_CONTENT_PROMPT,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROMPT,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    HTTP_RESPONSE_STATUS_CODE,
    OPERATION_CHAT,
    SERVER_ADDRESS,
)

logger = logging.getLogger(__name__)

_PROVIDER_HOSTS: Final[dict[str, str]] = {
    "openai": "api.openai.com",
    "anthropic": "api.anthropic.com",
}


def init_tracer(otlp_endpoint: str) -> None:
    """Initialize the global OTel tracer provider. Call once at startup."""
    resource = Resource({SERVICE_NAME: "llm-otel-sidecar"})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def _get_tracer() -> Tracer:
    return trace.get_tracer("llm.proxy")


def emit_span(parsed: ParsedSpan) -> None:
    """Build and export a single OTel span from a ParsedSpan. Never raises."""
    try:
        tracer = _get_tracer()
        with tracer.start_as_current_span(
            name=f"{parsed.provider}.chat",
            kind=SpanKind.CLIENT,
        ) as span:
            span.set_attribute(GEN_AI_SYSTEM, parsed.provider)
            span.set_attribute(GEN_AI_OPERATION_NAME, OPERATION_CHAT)
            span.set_attribute(GEN_AI_REQUEST_MODEL, parsed.model)
            span.set_attribute(GEN_AI_RESPONSE_MODEL, parsed.model)
            span.set_attribute(HTTP_RESPONSE_STATUS_CODE, parsed.status_code)

            server_address = _PROVIDER_HOSTS.get(parsed.provider, "")
            if server_address:
                span.set_attribute(SERVER_ADDRESS, server_address)

            if parsed.input_tokens is not None:
                span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, parsed.input_tokens)

            if parsed.output_tokens is not None:
                span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, parsed.output_tokens)

            if parsed.finish_reason is not None:
                span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, [parsed.finish_reason])

            if parsed.error_type is not None:
                span.set_attribute(ERROR_TYPE, parsed.error_type)
                span.set_status(Status(StatusCode.ERROR, parsed.error_type))

            if parsed.request_messages is not None:
                span.add_event(
                    GEN_AI_CONTENT_PROMPT,
                    {GEN_AI_PROMPT: json.dumps(parsed.request_messages)},
                )
    except Exception:
        logger.warning("emit_span failed", exc_info=True)
