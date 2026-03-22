# PRD — LLM OTel Sidecar

## Overview

A transparent reverse proxy that sits between any application and an LLM API (OpenAI, Anthropic), captures request/response telemetry, and emits OpenTelemetry spans conforming to the [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) spec. Zero application code changes required.

## Problem

Every existing LLM observability tool (Langfuse, Langtrace, Helicone) requires either:
- SDK wrapping (`langfuse.trace(...)` around every call), or
- Routing traffic through a hosted SaaS proxy

Platform engineers cannot instrument LLM calls at the infrastructure layer without touching application code. This is a gap — the same gap that OTel solved for HTTP, databases, and messaging systems.

## Solution

A local sidecar proxy that:
1. Accepts LLM API traffic on a local port
2. Forwards requests to the real upstream (OpenAI / Anthropic)
3. Captures request and response payloads
4. Emits OTel spans with GenAI semantic convention attributes
5. Ships spans to any OTLP-compatible backend (Jaeger, Grafana Tempo, Datadog, Honeycomb)

Applications point their `OPENAI_BASE_URL` or `ANTHROPIC_BASE_URL` env var at the sidecar. Nothing else changes.

## Goals (v1)

- [x] Proxy OpenAI Chat Completions API (`/v1/chat/completions`)
- [x] Proxy Anthropic Messages API (`/v1/messages`)
- [x] Capture: model, input tokens, output tokens, latency, finish reason, HTTP status
- [x] Emit OTel spans with GenAI semconv attributes
- [x] Export via OTLP (gRPC) to a local collector
- [x] Docker Compose setup: sidecar + Jaeger all-in-one for local development
- [x] Support streaming responses (`stream: true`) without breaking the stream

## Out of Scope (v1)

- Custom dashboard UI (use Jaeger's built-in UI)
- Prompt/response storage or replay
- PII redaction
- Cost attribution / tagging
- Multi-tenant / SaaS deployment
- Support for Azure OpenAI, Bedrock, Vertex (v2+)

## Success Criteria

- Proxy adds < 10ms p99 overhead vs direct API call
- Streaming responses work end-to-end (tokens reach the client as they arrive)
- A trace appears in Jaeger within 5 seconds of an LLM call completing
- The span contains all required GenAI semconv attributes (see Architecture doc)
- README setup takes < 5 minutes for a new developer

## Target Audience

**Primary:** Platform / SRE engineers who own LLM infrastructure and want observability without coupling to a vendor SDK.

**Secondary:** Individual developers who want to understand what their LLM calls are doing locally, without sending data to a third party.

## Key Design Constraints

- **No database.** Spans are emitted and forgotten. State lives in the OTel backend.
- **No auth.** The sidecar trusts whatever API key is in the forwarded request header. It does not store or log keys.
- **Passthrough fidelity.** The response body and headers reaching the client must be byte-for-byte identical to what the upstream returns, except for the added latency.
- **Single binary / single process.** `python -m llm_otel_sidecar` or `docker run`. No orchestration required for local use.

## User Journey (Happy Path)

```
1. Developer runs: docker compose up
2. Sets env var: OPENAI_BASE_URL=http://localhost:4000/openai
3. Runs their app as normal
4. Opens Jaeger UI at http://localhost:16686
5. Sees a trace per LLM call with token counts, latency, model name
```

## Non-Functional Requirements

| Requirement | Target |
|---|---|
| Proxy latency overhead | < 10ms p99 |
| Streaming correctness | Must not buffer — chunks forwarded as received |
| Memory footprint | < 50MB idle |
| Python version | 3.11+ |
| Startup time | < 2 seconds |
