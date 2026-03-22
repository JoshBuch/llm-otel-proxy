from __future__ import annotations

import json
import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from llm_otel_sidecar.parsers.base import ParsedSpan
from llm_otel_sidecar.telemetry import emitter
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


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    # Reset the global OTel provider so each test gets a fresh one.
    trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]

    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    trace.set_tracer_provider(provider)
    return exp


def _make_parsed(**kwargs) -> ParsedSpan:
    defaults = dict(
        provider="openai",
        model="gpt-4o",
        latency_ms=123.4,
        status_code=200,
        is_streaming=False,
    )
    defaults.update(kwargs)
    return ParsedSpan(**defaults)


# ---------------------------------------------------------------------------
# Test 1: Happy path — all required attributes present
# ---------------------------------------------------------------------------

def test_happy_path_required_attributes(memory_exporter: InMemorySpanExporter) -> None:
    parsed = _make_parsed(input_tokens=10, output_tokens=20)
    emitter.emit_span(parsed)

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    attrs = span.attributes
    assert span.name == "openai.chat"
    assert attrs[GEN_AI_SYSTEM] == "openai"
    assert attrs[GEN_AI_OPERATION_NAME] == OPERATION_CHAT
    assert attrs[GEN_AI_REQUEST_MODEL] == "gpt-4o"
    assert attrs[GEN_AI_RESPONSE_MODEL] == "gpt-4o"
    assert attrs[HTTP_RESPONSE_STATUS_CODE] == 200
    assert attrs[SERVER_ADDRESS] == "api.openai.com"
    assert attrs[GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert attrs[GEN_AI_USAGE_OUTPUT_TOKENS] == 20


# ---------------------------------------------------------------------------
# Test 2: input_tokens=None → attribute not set
# ---------------------------------------------------------------------------

def test_input_tokens_none_not_set(memory_exporter: InMemorySpanExporter) -> None:
    parsed = _make_parsed(input_tokens=None, output_tokens=5)
    emitter.emit_span(parsed)

    span = memory_exporter.get_finished_spans()[0]
    assert GEN_AI_USAGE_INPUT_TOKENS not in span.attributes


# ---------------------------------------------------------------------------
# Test 3: finish_reason=None → attribute not set
# ---------------------------------------------------------------------------

def test_finish_reason_none_not_set(memory_exporter: InMemorySpanExporter) -> None:
    parsed = _make_parsed(finish_reason=None)
    emitter.emit_span(parsed)

    span = memory_exporter.get_finished_spans()[0]
    assert GEN_AI_RESPONSE_FINISH_REASONS not in span.attributes


# ---------------------------------------------------------------------------
# Test 4: error_type set → span status ERROR and ERROR_TYPE attribute
# ---------------------------------------------------------------------------

def test_error_type_sets_status_and_attribute(memory_exporter: InMemorySpanExporter) -> None:
    parsed = _make_parsed(status_code=500, error_type="server_error")
    emitter.emit_span(parsed)

    span = memory_exporter.get_finished_spans()[0]
    assert span.attributes[ERROR_TYPE] == "server_error"
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description == "server_error"


# ---------------------------------------------------------------------------
# Test 5: error_type=None → span status not ERROR
# ---------------------------------------------------------------------------

def test_no_error_type_status_not_error(memory_exporter: InMemorySpanExporter) -> None:
    parsed = _make_parsed(error_type=None)
    emitter.emit_span(parsed)

    span = memory_exporter.get_finished_spans()[0]
    assert span.status.status_code != StatusCode.ERROR
    assert ERROR_TYPE not in span.attributes


# ---------------------------------------------------------------------------
# Test 6: request_messages set → span has prompt event with JSON content
# ---------------------------------------------------------------------------

def test_request_messages_adds_prompt_event(memory_exporter: InMemorySpanExporter) -> None:
    messages = [{"role": "user", "content": "Hello"}]
    parsed = _make_parsed(request_messages=messages)
    emitter.emit_span(parsed)

    span = memory_exporter.get_finished_spans()[0]
    events = span.events
    assert len(events) == 1
    event = events[0]
    assert event.name == GEN_AI_CONTENT_PROMPT
    assert GEN_AI_PROMPT in event.attributes
    assert json.loads(event.attributes[GEN_AI_PROMPT]) == messages


# ---------------------------------------------------------------------------
# Test 7: _get_tracer raises → emit_span does not raise, logs warning
# ---------------------------------------------------------------------------

def test_emit_span_does_not_raise_on_exception(
    memory_exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom() -> None:
        raise RuntimeError("tracer exploded")

    monkeypatch.setattr(emitter, "_get_tracer", boom)

    parsed = _make_parsed()
    with caplog.at_level(logging.WARNING, logger="llm_otel_sidecar.telemetry.emitter"):
        emitter.emit_span(parsed)  # must not raise

    assert any("emit_span failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Test 8: finish_reason set → GEN_AI_RESPONSE_FINISH_REASONS is a list/sequence
# ---------------------------------------------------------------------------

def test_finish_reason_is_list(memory_exporter: InMemorySpanExporter) -> None:
    parsed = _make_parsed(finish_reason="stop")
    emitter.emit_span(parsed)

    span = memory_exporter.get_finished_spans()[0]
    value = span.attributes[GEN_AI_RESPONSE_FINISH_REASONS]
    # OTel SDK stores sequences as tuples internally; check it is sequence-like and contains "stop"
    assert "stop" in value
    assert len(value) == 1


# ---------------------------------------------------------------------------
# Extra: anthropic provider sets correct server address
# ---------------------------------------------------------------------------

def test_anthropic_server_address(memory_exporter: InMemorySpanExporter) -> None:
    parsed = _make_parsed(provider="anthropic", model="claude-3-5-sonnet-20241022")
    emitter.emit_span(parsed)

    span = memory_exporter.get_finished_spans()[0]
    assert span.attributes[SERVER_ADDRESS] == "api.anthropic.com"
    assert span.name == "anthropic.chat"
