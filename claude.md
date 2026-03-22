# claude.md — LLM OTel Sidecar

This file is the first thing Claude Code should read. It contains all the context needed to contribute to this project without asking clarifying questions.

## What This Project Is

A transparent reverse proxy that sits in front of OpenAI and Anthropic APIs, captures LLM call telemetry, and emits OpenTelemetry spans using the GenAI semantic conventions spec. Zero SDK changes required in consuming applications — they just point their base URL at the sidecar.

Read `PRD.md` for the why. Read `ARCHITECTURE.md` for the full system design, file structure, and component specs before writing any code.

## Project Status

This is a greenfield project. Nothing has been built yet. Start from scratch following the structure in `ARCHITECTURE.md`.

## Engineering Principles

**Correctness over cleverness.** Streaming responses must never be buffered before forwarding. A span with incomplete attributes is better than a dropped response. When in doubt, let the response through and log the telemetry failure.

**Telemetry is best-effort.** The OTel export path must never be in the critical path of a response. Use `fire-and-forget` (background task) for span emission. A failure in the emitter should log a warning, never raise.

**No state, no storage.** This is a stateless proxy. No database, no disk writes, no caching. If something needs persistence, it belongs in the OTel backend (Jaeger, Tempo, etc.).

**Passthrough fidelity.** The response body, status code, and headers that reach the client must be identical to what the upstream returned. Never mutate the response.

## Tech Stack

- **Python 3.11+**
- **FastAPI** for the HTTP server
- **httpx** (async) for upstream forwarding
- **opentelemetry-sdk** + **opentelemetry-exporter-otlp-proto-grpc** for span emission
- **pydantic-settings** for config via env vars
- **pytest** + **pytest-asyncio** + **respx** for tests

Do not introduce additional dependencies without a strong reason. Keep the dependency footprint minimal.

## Repository Layout

```
src/llm_otel_sidecar/
├── main.py              # Entrypoint — uvicorn startup
├── config.py            # All env var config (pydantic-settings)
├── proxy/
│   ├── server.py        # FastAPI app, route registration
│   ├── openai.py        # /openai/* handler
│   └── anthropic.py     # /anthropic/* handler
├── parsers/
│   ├── base.py          # ParsedSpan dataclass
│   ├── openai.py        # OpenAI response → ParsedSpan
│   └── anthropic.py     # Anthropic response → ParsedSpan
└── telemetry/
    ├── conventions.py   # GenAI semconv attribute name constants
    └── emitter.py       # OTel tracer setup + span builder
```

## Coding Standards

**All async, all the way down.** Use `async def` for all route handlers and any function that touches I/O. Do not use `requests` — use `httpx.AsyncClient`.

**Type hints everywhere.** Every function signature must have full type annotations. Use `from __future__ import annotations` at the top of every file.

**Explicit over implicit.** Config values come from `config.py` only — never hardcode URLs, ports, or endpoint strings inline.

**Constants for semconv attributes.** Never write `"gen_ai.usage.input_tokens"` as a string literal in business logic. Use the constants from `telemetry/conventions.py`.

**ParsedSpan is the contract** between parsers and the emitter. Parsers return `ParsedSpan`. The emitter accepts `ParsedSpan`. Neither side knows about the other's implementation.

**Error handling pattern:**
```python
try:
    parsed = parse_response(request_data, response_data)
    background_tasks.add_task(emitter.emit, parsed)
except Exception as exc:
    logger.warning("Failed to emit span: %s", exc, exc_info=True)
# Always continue — never let telemetry block the response
```

## GenAI Semantic Convention Attributes

These are the attributes we emit on every span. Constants must live in `telemetry/conventions.py`.

```python
# Required
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"

# HTTP context
SERVER_ADDRESS = "server.address"
HTTP_RESPONSE_STATUS_CODE = "http.response.status_code"

# Error
ERROR_TYPE = "error.type"
```

## Streaming Implementation Notes

For streaming responses, the proxy must:
1. Open a streaming httpx request to upstream
2. Yield chunks to the client as they arrive — no buffering
3. Accumulate chunks in a side buffer (copy, not intercept)
4. After the stream ends, parse the accumulated buffer for telemetry
5. Emit the span

Use FastAPI's `StreamingResponse` with an async generator. The generator yields from the upstream stream while appending to the buffer simultaneously.

For OpenAI streams, the final usage is in the `[DONE]` event's `usage` field (only present when `stream_options: {"include_usage": true}` is set in the request — detect and add this automatically).

For Anthropic streams, usage is in the `message_delta` event at end of stream.

## How to Run Locally

```bash
# Start Jaeger + sidecar
docker compose up

# Or run sidecar directly
pip install -e ".[dev]"
python -m llm_otel_sidecar

# Point your app at the sidecar
export OPENAI_BASE_URL=http://localhost:4000/openai
export ANTHROPIC_BASE_URL=http://localhost:4000/anthropic

# View traces
open http://localhost:16686
```

## How to Run Tests

```bash
pytest tests/unit/          # fast, no I/O
pytest tests/integration/   # uses respx mocks, no real API calls
pytest                      # all tests
```

## Build Order Recommendation

Build in this sequence to keep each step testable:

1. `config.py` — settings dataclass, nothing else
2. `parsers/base.py` — `ParsedSpan` dataclass
3. `parsers/openai.py` + unit tests against fixture JSON
4. `parsers/anthropic.py` + unit tests against fixture JSON
5. `telemetry/conventions.py` — just constants
6. `telemetry/emitter.py` — tracer init + span builder (mock exporter in tests)
7. `proxy/server.py` — FastAPI app skeleton, health check route
8. `proxy/openai.py` — non-streaming handler first, then streaming
9. `proxy/anthropic.py` — same pattern
10. `main.py` — wires everything together
11. `docker-compose.yml` + `Dockerfile`
12. Integration tests

Do not jump ahead to docker-compose before the proxy layer works in isolation.

## Known Edge Cases to Handle

- OpenAI streaming only includes usage if `stream_options.include_usage` is `true` — inject this into the forwarded request transparently
- Anthropic returns `stop_reason` not `finish_reason` — normalize to `finish_reason` in `ParsedSpan`
- Some models return `null` for usage on certain error responses — handle `None` gracefully in the parser
- The `model` in the response may differ from the `model` in the request (e.g. when sending `gpt-4` the response may say `gpt-4-0613`) — capture both as `request.model` and `response.model`
- Large request bodies (e.g. long context windows) should not be read into memory twice — read once, store in a variable, forward from that variable

## Definition of Done

A task is complete when:
- Code is written and passes type checking (`mypy` or pyright)
- Unit tests exist for any function containing logic (parsers, emitter)
- No hardcoded strings that belong in `config.py` or `conventions.py`
- `docker compose up` + a real API call produces a visible trace in Jaeger UI
