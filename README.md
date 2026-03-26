# llm-otel-proxy

> **Zero-code LLM observability. Drop it in front of OpenAI or Anthropic, change one env var, done.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-GenAI%20semconv-blueviolet)](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
[![Docker](https://img.shields.io/badge/docker-compose%20up-2496ED?logo=docker)](docker-compose.yml)

---

Every LLM observability tool today asks you to wrap your SDK:

```python
# What every tool asks you to do
with langfuse.trace() as t:
    response = client.chat.completions.create(...)  # 😩 touching every callsite
```

**This one doesn't.** It's a transparent proxy. Point your `base_url` at it, and every call is automatically traced — tokens, latency, model, finish reason — with zero changes to application code.

```bash
# Before
OPENAI_BASE_URL=https://api.openai.com

# After — that's literally it
OPENAI_BASE_URL=http://localhost:4000/openai
```

---

## What you get

A full **LLM Observatory** dashboard out of the box:

![LLM Observatory Dashboard](docs/screenshots/dashboard-full.png)

| Panel | What it shows |
|---|---|
| **Total Requests** | Span count across the selected time window |
| **Total Input Tokens** | Cumulative prompt tokens consumed |
| **Total Output Tokens** | Cumulative completion tokens generated |
| **p95 Latency** | 95th-percentile end-to-end latency |
| **Error Rate** | % of requests returning non-2xx |
| **Token Usage Over Time** | Input/output tokens as a time series |
| **Requests / Minute** | Live request rate |
| **Latency Percentiles** | p50 / p95 / p99 over time |
| **Requests by Model** | Breakdown by model name |
| **Finish Reasons** | stop / length / content_filter distribution |

Every LLM call also produces a full distributed trace in Grafana Tempo with all [OpenTelemetry GenAI semantic convention](https://opentelemetry.io/docs/specs/semconv/gen-ai/) attributes:

```
Span: openai.chat  [200 OK, 1.4s]
  gen_ai.system                  = openai
  gen_ai.request.model           = gpt-4o
  gen_ai.response.model          = gpt-4o-2024-08-06
  gen_ai.usage.input_tokens      = 1176
  gen_ai.usage.output_tokens     = 312
  gen_ai.response.finish_reasons = ["stop"]
  http.response.status_code      = 200
  server.address                 = api.openai.com
```

---

## Quickstart

```bash
git clone https://github.com/JoshBuch/llm-otel-proxy.git
cd llm-otel-proxy
docker compose up
```

That's it. The full stack comes up:

| Service | URL | Purpose |
|---|---|---|
| **Proxy** | `http://localhost:4000` | Drop-in LLM endpoint |
| **Grafana** | `http://localhost:3000` | Pre-built dashboard (admin/admin) |
| **Tempo** | `http://localhost:3200` | Distributed trace storage |
| **Prometheus** | `http://localhost:9090` | Span metrics |

Then point your app at the proxy — **no other changes**:

```bash
# OpenAI
export OPENAI_BASE_URL=http://localhost:4000/openai/v1

# Anthropic
export ANTHROPIC_BASE_URL=http://localhost:4000/anthropic
```

---

## Works with every LLM SDK and language

**Python — OpenAI**
```python
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url="http://localhost:4000/openai/v1",  # only change
)
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
)
```

**Python — Anthropic**
```python
import anthropic

client = anthropic.Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    base_url="http://localhost:4000/anthropic",  # only change
)
```

**TypeScript / Node.js**
```typescript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://localhost:4000/openai/v1",  // only change
});
```

**curl**
```bash
curl http://localhost:4000/openai/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hi"}]}'
```

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  Your application  (any language, any LLM SDK)        │
│  OPENAI_BASE_URL=http://localhost:4000/openai         │
└───────────────────────┬──────────────────────────────┘
                        │ unchanged HTTP request
                        ▼
┌──────────────────────────────────────────────────────┐
│  llm-otel-proxy  (FastAPI + httpx)                   │
│                                                      │
│  Proxy ──▶ Parser ──▶ OTel Emitter (OTLP gRPC)      │
│     │         └─ ParsedSpan (semconv fields)         │
│     │ forward                       │ spans          │
└─────┼─────────────────────────────-─┼────────────────┘
      │                               │
      ▼                               ▼
 OpenAI / Anthropic        OTel Collector (contrib)
                              │              │
                              ▼              ▼
                           Tempo        Prometheus
                         (traces)    (span metrics)
                              └──────┬───────┘
                                     ▼
                                  Grafana
                           (LLM Observatory dashboard)
```

**Key design invariants:**
- **Streaming is never buffered.** Chunks are forwarded to the client immediately; a side-copy is accumulated for telemetry after the stream ends.
- **Telemetry is fire-and-forget.** Span emission runs in a background task and never delays or fails the response.
- **Byte-for-byte passthrough.** Status code, headers, and body are identical to what the upstream returned.

---

## Configuration

All configuration is via environment variables — no config files to edit.

| Variable | Default | Description |
|---|---|---|
| `SIDECAR_PORT` | `4000` | Port the proxy listens on |
| `OTLP_ENDPOINT` | `http://localhost:4317` | OTLP gRPC collector endpoint |
| `OPENAI_UPSTREAM` | `https://api.openai.com` | OpenAI upstream base URL |
| `ANTHROPIC_UPSTREAM` | `https://api.anthropic.com` | Anthropic upstream base URL |
| `LOG_LEVEL` | `INFO` | Python log level |
| `CAPTURE_PROMPTS` | `false` | Attach full prompt/completion text as span events (privacy opt-in) |

`CAPTURE_PROMPTS=true` adds `gen_ai.content.prompt` and `gen_ai.content.completion` span events. **Off by default** — turn on only in environments where you're comfortable storing prompt content.

---

## Send traces to your existing backend

The proxy ships spans via OTLP gRPC. `OTLP_ENDPOINT` works with any OTLP-compatible collector:

```bash
# Grafana Cloud (replace with your endpoint)
OTLP_ENDPOINT=https://otlp-gateway-prod-eu-west-0.grafana.net/otlp

# Honeycomb
OTLP_ENDPOINT=https://api.honeycomb.io:443
# + OTEL_EXPORTER_OTLP_HEADERS="x-honeycomb-team=<api-key>"

# Datadog Agent
OTLP_ENDPOINT=http://datadog-agent:4317

# Jaeger (all-in-one)
OTLP_ENDPOINT=http://localhost:4317

# Self-hosted Grafana Tempo
OTLP_ENDPOINT=http://tempo:4317
```

---

## Run without Docker

```bash
pip install -e ".[dev]"

# Point at any OTLP backend
OTLP_ENDPOINT=http://localhost:4317 python -m llm_otel_sidecar
```

---

## Why not just use an SDK wrapper?

| | llm-otel-proxy | Langfuse / Langtrace / Helicone |
|---|---|---|
| Code changes required | **None** | SDK wrapping at every callsite |
| Works with any language | **Yes** | Python / JS only (mostly) |
| Vendor lock-in | **None** (OTLP standard) | Proprietary format / SaaS |
| Self-hostable | **Yes** | Partial / complex |
| Streaming support | **Yes** | Varies |
| OTel GenAI semconv | **Yes** (native) | Rarely |

Platform engineers can instrument an entire fleet of services by changing one env var per deployment — no application code PRs required.

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Unit tests — pure logic, no I/O
pytest tests/unit/

# Integration tests — httpx mocks, no real API calls
pytest tests/integration/

# All tests
pytest
```

---

## Supported providers

| Provider | Non-streaming | Streaming | Token tracking |
|---|---|---|---|
| OpenAI (`/v1/chat/completions`) | ✅ | ✅ | ✅ |
| Anthropic (`/v1/messages`) | ✅ | ✅ | ✅ |

## Roadmap

- [ ] Azure OpenAI endpoint support
- [ ] Amazon Bedrock support
- [ ] Google Vertex AI / Gemini support
- [ ] Cost attribution (token → USD) per model
- [ ] PII redaction for `CAPTURE_PROMPTS` mode
- [ ] Kubernetes sidecar + Helm chart
- [ ] CI/CD: pytest + mypy in GitHub Actions

---

## Contributing

Issues and PRs welcome. The codebase is intentionally small — the core proxy is ~200 lines across `proxy/openai.py` and `proxy/anthropic.py`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for a full walkthrough of how each component fits together.

---

## License

[MIT](LICENSE) — use it, fork it, embed it.
