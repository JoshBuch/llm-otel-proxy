# Architecture — LLM OTel Sidecar

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Application (any language)                                     │
│  OPENAI_BASE_URL=http://localhost:4000/openai                   │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP POST /v1/chat/completions
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  LLM OTel Sidecar  (FastAPI + httpx)                            │
│                                                                 │
│  ┌──────────────┐   ┌─────────────┐   ┌─────────────────────┐  │
│  │  Proxy Layer │──▶│   Parser    │──▶│   OTel Emitter      │  │
│  │  (intercept) │   │ (semconv)   │   │   (OTLP gRPC)       │  │
│  └──────┬───────┘   └─────────────┘   └─────────────────────┘  │
│         │ forward                              │ spans           │
└─────────┼────────────────────────────────────-┼─────────────────┘
          │                                      │
          ▼                                      ▼
  ┌───────────────┐                   ┌──────────────────────┐
  │  OpenAI API   │                   │  OTLP Collector      │
  │  Anthropic API│                   │  (Jaeger all-in-one) │
  └───────────────┘                   └──────────────────────┘
```

## Repository Structure

```
llm-otel-sidecar/
├── claude.md                    # Claude Code context (read first)
├── PRD.md
├── ARCHITECTURE.md
│
├── src/
│   └── llm_otel_sidecar/
│       ├── __init__.py
│       ├── main.py              # Entrypoint — starts FastAPI server
│       ├── config.py            # Settings via env vars (pydantic-settings)
│       │
│       ├── proxy/
│       │   ├── __init__.py
│       │   ├── server.py        # FastAPI app + route registration
│       │   ├── openai.py        # /openai/* route handler
│       │   └── anthropic.py     # /anthropic/* route handler
│       │
│       ├── parsers/
│       │   ├── __init__.py
│       │   ├── base.py          # ParsedSpan dataclass
│       │   ├── openai.py        # Extracts semconv fields from OpenAI response
│       │   └── anthropic.py     # Extracts semconv fields from Anthropic response
│       │
│       └── telemetry/
│           ├── __init__.py
│           ├── conventions.py   # GenAI semconv attribute name constants
│           └── emitter.py       # Builds + exports OTel spans
│
├── tests/
│   ├── unit/
│   │   ├── test_openai_parser.py
│   │   └── test_anthropic_parser.py
│   └── integration/
│       └── test_proxy.py        # Uses httpx.AsyncClient against live proxy
│
├── docker-compose.yml           # sidecar + jaeger-all-in-one
├── Dockerfile
├── pyproject.toml
└── README.md
```

## Component Design

### 1. Proxy Layer (`proxy/`)

Built on **FastAPI** with **httpx.AsyncClient** for upstream forwarding.

Two route handlers are registered:
- `POST /openai/{path:path}` — strips `/openai` prefix, forwards to `https://api.openai.com`
- `POST /anthropic/{path:path}` — strips `/anthropic` prefix, forwards to `https://api.anthropic.com`

**Non-streaming flow:**
```
1. Receive request, record start_time
2. Forward request body + headers to upstream via httpx
3. Await full response
4. Record end_time, compute latency_ms
5. Pass request + response to parser
6. Pass ParsedSpan to emitter (fire-and-forget, non-blocking)
7. Return upstream response to client unchanged
```

**Streaming flow (`stream: true`):**
```
1. Receive request, record start_time
2. Open streaming httpx request to upstream
3. Buffer SSE chunks as they arrive
4. Yield each chunk to client immediately (no buffering delay)
5. On stream end, parse accumulated chunks
6. Emit span (fire-and-forget)
```

Key constraint: **never buffer a streaming response before forwarding.** Use `StreamingResponse` + `async for chunk in upstream_response.aiter_bytes()`.

### 2. Parsers (`parsers/`)

Each parser takes the raw request dict and response dict and returns a `ParsedSpan`.

```python
@dataclass
class ParsedSpan:
    provider: str            # "openai" | "anthropic"
    model: str               # e.g. "gpt-4o"
    input_tokens: int | None
    output_tokens: int | None
    finish_reason: str | None
    latency_ms: float
    status_code: int
    error_type: str | None   # set on non-2xx responses
    is_streaming: bool
    request_messages: list[dict] | None   # captured for span events (optional)
```

**OpenAI response fields:**
- `response["model"]` → model
- `response["usage"]["prompt_tokens"]` → input_tokens
- `response["usage"]["completion_tokens"]` → output_tokens
- `response["choices"][0]["finish_reason"]` → finish_reason

**Anthropic response fields:**
- `response["model"]` → model
- `response["usage"]["input_tokens"]` → input_tokens
- `response["usage"]["output_tokens"]` → output_tokens
- `response["stop_reason"]` → finish_reason

Streaming: accumulate `delta` chunks and reconstruct `usage` from the final `[DONE]` event or `message_delta` event.

### 3. OTel Emitter (`telemetry/`)

Uses `opentelemetry-sdk` with an OTLP gRPC exporter.

**Tracer setup (singleton, initialized at startup):**
```python
resource = Resource({SERVICE_NAME: "llm-otel-sidecar"})
provider = TracerProvider(resource=resource)
exporter = OTLPSpanExporter(endpoint=config.otlp_endpoint)  # default: localhost:4317
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("llm.proxy")
```

**Span structure per call:**
```
Span name:   "{provider}.chat" (e.g. "openai.chat")
Span kind:   CLIENT
Start time:  request received
End time:    response fully received

Attributes (GenAI semconv):
  gen_ai.system                = "openai" | "anthropic"
  gen_ai.request.model         = "gpt-4o"
  gen_ai.response.model        = "gpt-4o"  (may differ from request)
  gen_ai.usage.input_tokens    = 123
  gen_ai.usage.output_tokens   = 456
  gen_ai.response.finish_reasons = ["stop"]
  gen_ai.operation.name        = "chat"
  server.address               = "api.openai.com"
  http.response.status_code    = 200

On error:
  error.type                   = "rate_limit_error" | "invalid_request_error" | ...
  otel.status_code             = "ERROR"
  otel.status_description      = upstream error message
```

### 4. Config (`config.py`)

All configuration via environment variables using `pydantic-settings`:

| Env Var | Default | Description |
|---|---|---|
| `SIDECAR_PORT` | `4000` | Port the sidecar listens on |
| `OTLP_ENDPOINT` | `http://localhost:4317` | OTLP gRPC collector endpoint |
| `OPENAI_UPSTREAM` | `https://api.openai.com` | OpenAI upstream base URL |
| `ANTHROPIC_UPSTREAM` | `https://api.anthropic.com` | Anthropic upstream base URL |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `CAPTURE_PROMPTS` | `false` | Whether to attach prompt text as span events (opt-in) |

## GenAI Semantic Convention Reference

Spec: https://opentelemetry.io/docs/specs/semconv/gen-ai/

**Stable attributes (v1.29+):**

| Attribute | Type | Source |
|---|---|---|
| `gen_ai.system` | string | config (provider name) |
| `gen_ai.operation.name` | string | always `"chat"` for v1 |
| `gen_ai.request.model` | string | request body |
| `gen_ai.response.model` | string | response body |
| `gen_ai.usage.input_tokens` | int | response usage |
| `gen_ai.usage.output_tokens` | int | response usage |
| `gen_ai.response.finish_reasons` | string[] | response choices |

**Optional span events (when `CAPTURE_PROMPTS=true`):**

| Event | Attribute |
|---|---|
| `gen_ai.content.prompt` | `gen_ai.prompt` = full messages JSON |
| `gen_ai.content.completion` | `gen_ai.completion` = response text |

## Docker Compose Setup

```yaml
# docker-compose.yml
services:
  sidecar:
    build: .
    ports:
      - "4000:4000"
    environment:
      - OTLP_ENDPOINT=http://jaeger:4317
    depends_on:
      - jaeger

  jaeger:
    image: jaegertracing/all-in-one:latest
    ports:
      - "16686:16686"   # Jaeger UI
      - "4317:4317"     # OTLP gRPC receiver
    environment:
      - COLLECTOR_OTLP_ENABLED=true
```

## Key Dependencies

```toml
[project]
requires-python = ">=3.11"

dependencies = [
  "fastapi>=0.111",
  "uvicorn[standard]>=0.29",
  "httpx>=0.27",
  "opentelemetry-sdk>=1.24",
  "opentelemetry-exporter-otlp-proto-grpc>=1.24",
  "pydantic-settings>=2.2",
]

[project.optional-dependencies]
dev = [
  "pytest>=8",
  "pytest-asyncio>=0.23",
  "respx>=0.21",   # httpx mock library
]
```

## Error Handling Strategy

- **Upstream 4xx/5xx**: forward status to client unchanged, set `error.type` on span, mark span status ERROR
- **Upstream timeout**: return 504 to client, emit span with error
- **Parse failure**: log warning, emit partial span (with what we have), never drop the response
- **OTel export failure**: log warning, continue — telemetry is best-effort, never block the response path

## Testing Strategy

**Unit tests** — pure functions, no I/O:
- `test_openai_parser.py`: feed fixture JSON responses, assert `ParsedSpan` fields
- `test_anthropic_parser.py`: same for Anthropic

**Integration tests** — use `respx` to mock the upstream:
- Assert proxy forwards correct headers/body
- Assert span is emitted with correct attributes
- Assert streaming responses are forwarded chunk-by-chunk
- Assert error responses produce ERROR-status spans
