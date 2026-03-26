# llm-otel-proxy

**Zero-code LLM observability using OpenTelemetry.**

A transparent sidecar proxy that sits between your application and the OpenAI / Anthropic APIs. It captures every request and response, emits [OpenTelemetry GenAI semantic convention](https://opentelemetry.io/docs/specs/semconv/gen-ai/) spans, and forwards the response to your app unchanged — with no SDK changes, no wrapping, no vendor lock-in.

```
Your App  →  llm-otel-proxy :4000  →  api.openai.com / api.anthropic.com
                    │
                    └──▶  OTel Collector  →  Grafana / Jaeger / Datadog / Honeycomb
```

---

## Why this exists

Every LLM observability tool today requires you to either:
- Wrap your SDK calls (`langfuse.trace(...)`, `tracer.start(...)`)
- Route traffic through a hosted SaaS proxy

This is the **infrastructure-layer** alternative. One env var change. Nothing else.

```bash
# Before
OPENAI_BASE_URL=https://api.openai.com

# After — that's it
OPENAI_BASE_URL=http://localhost:4000/openai
```

---

## Quickstart (Docker Compose)

Spins up the proxy, OTel Collector, Grafana Tempo (traces), Prometheus (metrics), and a pre-built Grafana dashboard.

```bash
git clone https://github.com/JoshBuch/llm-otel-proxy.git
cd llm-otel-proxy
docker compose up
```

Then point your app at the proxy:

| Provider  | Original base URL               | Proxy base URL                       |
|-----------|---------------------------------|--------------------------------------|
| OpenAI    | `https://api.openai.com`        | `http://localhost:4000/openai`        |
| Anthropic | `https://api.anthropic.com`     | `http://localhost:4000/anthropic`     |

Open the **LLM Observatory** dashboard at [http://localhost:30025](http://localhost:30025) (default login: `admin` / `admin`).

---

## What you get

Every LLM call produces an OTel span with these attributes:

| Attribute | Example value |
|---|---|
| `gen_ai.system` | `openai` |
| `gen_ai.operation.name` | `chat` |
| `gen_ai.request.model` | `gpt-4o` |
| `gen_ai.response.model` | `gpt-4o` |
| `gen_ai.usage.input_tokens` | `1176` |
| `gen_ai.usage.output_tokens` | `312` |
| `gen_ai.response.finish_reasons` | `["stop"]` |
| `http.response.status_code` | `200` |
| `server.address` | `api.openai.com` |

And the Grafana dashboard surfaces:

- Total requests, input tokens, output tokens
- p50 / p95 / p99 latency
- Error rate
- Requests per minute over time
- Token usage over time
- Requests broken down by model
- Finish reason breakdown

---

## Configuration

All settings are environment variables:

| Variable | Default | Description |
|---|---|---|
| `SIDECAR_PORT` | `4000` | Port the proxy listens on |
| `OTLP_ENDPOINT` | `http://localhost:4317` | OTLP gRPC collector endpoint |
| `OPENAI_UPSTREAM` | `https://api.openai.com` | OpenAI upstream base URL |
| `ANTHROPIC_UPSTREAM` | `https://api.anthropic.com` | Anthropic upstream base URL |
| `LOG_LEVEL` | `INFO` | Python log level |
| `CAPTURE_PROMPTS` | `false` | Attach prompt text as span events (privacy opt-in) |

`CAPTURE_PROMPTS=true` adds `gen_ai.content.prompt` / `gen_ai.content.completion` span events containing the full message text. Off by default.

---

## Running without Docker

```bash
pip install -e ".[dev]"
python -m llm_otel_sidecar
```

Point `OTLP_ENDPOINT` at any OTLP-compatible backend — Jaeger, Grafana Alloy, Datadog Agent, Honeycomb, etc.

---

## Code examples

**Python (OpenAI SDK)**
```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="http://localhost:4000/openai/v1",
)
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
)
```

**Python (Anthropic SDK)**
```python
import anthropic

client = anthropic.Anthropic(
    api_key="sk-ant-...",
    base_url="http://localhost:4000/anthropic",
)
message = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
```

**Node.js (OpenAI SDK)**
```typescript
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
  baseURL: "http://localhost:4000/openai/v1",
});
```

---

## Sending spans to a different backend

The proxy ships spans via OTLP gRPC. To send to a different backend, change `OTLP_ENDPOINT` to point at any OTLP-compatible collector:

```bash
# Jaeger (all-in-one)
OTLP_ENDPOINT=http://localhost:4317

# Grafana Alloy / Tempo
OTLP_ENDPOINT=http://alloy:4317

# Datadog Agent (OTLP intake)
OTLP_ENDPOINT=http://datadog-agent:4317

# Honeycomb
OTLP_ENDPOINT=https://api.honeycomb.io:443
# (also set OTEL_EXPORTER_OTLP_HEADERS="x-honeycomb-team=<api-key>")
```

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full component design, data flow, and testing strategy.

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Unit tests (no I/O)
pytest tests/unit/

# Integration tests (httpx mocks, no real API calls)
pytest tests/integration/

# All tests
pytest
```

---

## Roadmap

- [ ] Azure OpenAI support
- [ ] Amazon Bedrock support
- [ ] Google Vertex AI / Gemini support
- [ ] Cost attribution (token → USD) per model
- [ ] PII redaction for `CAPTURE_PROMPTS` mode
- [ ] Kubernetes sidecar deployment guide

---

## License

[MIT](LICENSE)
