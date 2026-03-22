# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

A transparent reverse proxy (Python/FastAPI) that intercepts calls to OpenAI and Anthropic APIs, emits OpenTelemetry spans using GenAI semantic conventions, and forwards responses unchanged. Consuming applications only need to change their base URL — no SDK changes.

See `ARCHITECTURE.md` for full system design and `PRD.md` for the why.

## Commands

```bash
# Install
pip install -e ".[dev]"

# Run sidecar
python -m llm_otel_sidecar

# Run with Docker + Jaeger
docker compose up

# Tests
pytest tests/unit/          # pure logic, no I/O
pytest tests/integration/   # uses respx mocks, no real API calls
pytest                      # all

# View traces
open http://localhost:16686  # Jaeger UI
```

## Architecture

```
Application → /openai/* or /anthropic/* → Proxy Layer → Upstream API
                                              │
                                          Parser (→ ParsedSpan)
                                              │
                                          OTel Emitter (OTLP gRPC, fire-and-forget)
                                              │
                                          Jaeger / any OTLP backend
```

**Source layout:** `src/llm_otel_sidecar/`
- `config.py` — all env var settings via pydantic-settings
- `proxy/server.py` — FastAPI app; `proxy/openai.py`, `proxy/anthropic.py` — route handlers
- `parsers/base.py` — `ParsedSpan` dataclass (contract between parsers and emitter)
- `parsers/openai.py`, `parsers/anthropic.py` — extract semconv fields from raw response dicts
- `telemetry/conventions.py` — GenAI semconv attribute name constants (never inline these strings)
- `telemetry/emitter.py` — builds + exports OTel spans

## Key Invariants

- **Streaming is never buffered before forwarding.** Yield chunks immediately via `StreamingResponse`; accumulate a side-copy for telemetry after the stream ends.
- **Telemetry is fire-and-forget.** Wrap span emission in `background_tasks.add_task(...)`. A failure must log a warning and never raise — the response always goes through.
- **Response passthrough fidelity.** Body, status code, and headers must be byte-for-byte identical to upstream.
- **No state, no storage.** Stateless proxy only; persistence belongs in the OTel backend.

## Environment Variables

| Var | Default | Purpose |
|-----|---------|---------|
| `SIDECAR_PORT` | `4000` | Listening port |
| `OTLP_ENDPOINT` | `http://localhost:4317` | OTLP gRPC collector |
| `OPENAI_UPSTREAM` | `https://api.openai.com` | OpenAI base URL |
| `ANTHROPIC_UPSTREAM` | `https://api.anthropic.com` | Anthropic base URL |
| `LOG_LEVEL` | `INFO` | Python log level |
| `CAPTURE_PROMPTS` | `false` | Attach prompt text as span events (privacy opt-in) |

## Coding Standards

- `async def` everywhere — no `requests`, only `httpx.AsyncClient`
- Full type annotations on every function; `from __future__ import annotations` at top of every file
- Config values from `config.py` only — no inline URLs or ports
- Semconv attribute strings only from `telemetry/conventions.py` constants

## Streaming Edge Cases

- **OpenAI:** inject `stream_options: {"include_usage": true}` transparently; usage arrives in the final `[DONE]` event
- **Anthropic:** usage is in the `message_delta` event at end of stream; `stop_reason` maps to `finish_reason` in `ParsedSpan`
- **Both:** `model` in response may differ from request — capture both as `request.model` / `response.model`
- Read the request body once into a variable; do not re-read it for forwarding

## Build Order

If building from scratch, follow this sequence (each step is independently testable):

1. `config.py` → 2. `parsers/base.py` → 3-4. parsers + unit tests → 5. `telemetry/conventions.py` → 6. `telemetry/emitter.py` → 7. `proxy/server.py` (health check) → 8-9. proxy handlers (non-streaming first, then streaming) → 10. `main.py` → 11. Docker → 12. integration tests

## Definition of Done

- Passes type checking (`mypy` or `pyright`)
- Unit tests cover all logic-bearing functions
- No hardcoded strings that belong in `config.py` or `conventions.py`
- `docker compose up` + a real API call produces a visible trace in Jaeger UI
