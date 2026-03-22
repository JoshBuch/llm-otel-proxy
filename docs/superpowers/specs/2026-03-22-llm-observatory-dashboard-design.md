# LLM Observatory Dashboard — Design Spec

**Date:** 2026-03-22
**Status:** Approved

## Overview

Replace the existing Jaeger backend with a full Grafana observability stack that gives platform engineers aggregate LLM metrics (token usage, latency percentiles, error rates, model breakdown) on a pre-provisioned dashboard — out of the box with `docker compose up`. Zero changes to the sidecar application code.

## Goals

- Platform engineers get a meaningful dashboard on first boot, no manual setup
- Aggregate time-series views (tokens/hour, p95 latency, request rate) alongside individual trace search
- `docker compose up` is the entire setup story — no external accounts, no SaaS

## Out of Scope (v1)

- Grafana alerting rules
- Cost estimation panels (token prices vary by tier and change frequently)
- Multi-tenant / per-team breakdowns
- Log correlation
- Any changes to sidecar application code (`emitter.py`, parsers, proxy handlers)

---

## Architecture

```
Application
    │ HTTP POST
    ▼
LLM Sidecar :4000          (unchanged)
    │ OTLP spans (gRPC)
    ▼
OTel Collector :4317        (new — replaces Jaeger as OTLP receiver)
    ├─ traces ──────────▶  Grafana Tempo :4318   (OTLP HTTP ingress)
    │                      Grafana Tempo :3200   (query API — used by Grafana datasource)
    └─ spanmetrics ──────▶  Prometheus :9090      (metrics storage)
                                │
                                ▼
                           Grafana :3000
                           ├─ datasource: Tempo   (trace search)
                           ├─ datasource: Prometheus (metrics panels)
                           └─ LLM Observatory dashboard (pre-provisioned)
```

Jaeger is removed. The sidecar's `OTLP_ENDPOINT` now points at the OTel Collector instead of Jaeger — same protocol, different receiver.

---

## Components

### OTel Collector (`otel-collector.yml`)

Receives OTLP spans from the sidecar and fans out to two backends:

**Traces pipeline:** forwards raw spans to Grafana Tempo via OTLP HTTP.

**Metrics pipeline:** uses the `spanmetrics` connector to auto-generate Prometheus metrics from span attributes. No extra instrumentation needed in the sidecar.

spanmetrics configuration:
- `dimensions`: `gen_ai.system`, `gen_ai.request.model`, `gen_ai.response.finish_reasons`
- `histogram.explicit.buckets`: `[100ms, 250ms, 500ms, 1s, 2s, 5s]`

Generated metrics:
- `traces_span_metrics_calls_total` — labelled by provider, model, finish_reason, status_code
- `traces_span_metrics_duration_milliseconds` — histogram for latency percentiles
- `traces_span_metrics_duration_milliseconds_sum/count` — for average latency

The collector exposes a Prometheus scrape endpoint on `:8889`.

### Grafana Tempo (`tempo.yml`)

Minimal single-binary Tempo configuration. Receives traces from the OTel Collector via OTLP HTTP on `:4318`. Stores traces in local filesystem within the container (ephemeral — appropriate for local/dev use).

### Prometheus (`prometheus.yml`)

Scrapes the OTel Collector's spanmetrics endpoint at `otel-collector:8889` every 15 seconds.

### Grafana (provisioning via volume mounts)

Auto-configures on first boot from the `grafana/provisioning/` directory:

**Datasources** (`grafana/provisioning/datasources/datasources.yml`):
- Tempo datasource pointing at `http://tempo:3200`
- Prometheus datasource pointing at `http://prometheus:9090`

**Dashboard provisioning** (`grafana/provisioning/dashboards/dashboards.yml`):
- Watches `grafana/dashboards/` for JSON files
- Auto-loads `llm-observatory.json` on startup

Default credentials: `admin / admin` (Grafana prompts to change on first login).

### Dashboard (`grafana/dashboards/llm-observatory.json`)

Pre-built Grafana dashboard JSON with the following panel layout:

**Row 1 — KPI stat panels (last 24h)**
- Total Requests — source: Prometheus (`traces_span_metrics_calls_total`)
- Total Input Tokens — source: Tempo TraceQL (`{ } | sum_over_time(span.gen_ai.usage.input_tokens)`)
- Total Output Tokens — source: Tempo TraceQL (`{ } | sum_over_time(span.gen_ai.usage.output_tokens)`)
- p95 Latency — source: Prometheus (histogram quantile on `traces_span_metrics_duration_milliseconds`)
- Error Rate % — source: Prometheus (rate of `status_code="STATUS_CODE_ERROR"` calls)

**Row 2 — Time series**
- Input + Output Tokens per hour (stacked area) — source: Tempo TraceQL metrics (`sum_over_time`)
- Requests per minute — source: Prometheus (`rate(traces_span_metrics_calls_total[1m])`)

**Row 3 — Analysis**
- Latency percentiles over time (p50 / p95 / p99) — source: Prometheus histogram
- Requests by model (horizontal bar chart) — source: Prometheus (`calls_total` by `gen_ai_request_model` label)
- Finish reasons distribution (stat panel) — source: Prometheus (`calls_total` by `gen_ai_response_finish_reasons` label)

**Row 4 — Trace exploration**
- Grafana Explore link to Tempo trace search (pre-filtered to `llm-otel-sidecar` service)

**Data source mapping:**
- Request counts, latency percentiles, model/finish reason breakdowns → Prometheus (spanmetrics)
- Token aggregates (sum of integer span attributes) → Tempo TraceQL metrics (requires Tempo 2.3+, which `grafana/tempo:latest` satisfies)
- Individual trace inspection → Tempo datasource via Grafana Explore

---

## File Changes

### New files

| File | Purpose |
|------|---------|
| `otel-collector.yml` | OTel Collector pipeline config |
| `prometheus.yml` | Prometheus scrape config |
| `tempo.yml` | Grafana Tempo config |
| `grafana/provisioning/datasources/datasources.yml` | Auto-connect Tempo + Prometheus |
| `grafana/provisioning/dashboards/dashboards.yml` | Auto-load dashboard JSON |
| `grafana/dashboards/llm-observatory.json` | Pre-built LLM Observatory dashboard |

### Modified files

| File | Change |
|------|--------|
| `docker-compose.yml` | Remove `jaeger` service; add `otel-collector`, `tempo`, `prometheus`, `grafana` services |

### Unchanged

All files under `src/llm_otel_sidecar/` — no application code changes.

---

## docker-compose Services

```yaml
services:
  sidecar:        # existing — OTLP_ENDPOINT points at otel-collector instead of jaeger
  otel-collector: # otel/opentelemetry-collector-contrib:latest — needs contrib for spanmetrics
  tempo:          # grafana/tempo:latest
  prometheus:     # prom/prometheus:latest
  grafana:        # grafana/grafana:latest — mounts provisioning/ and dashboards/
```

Key change to sidecar environment:
```yaml
- OTLP_ENDPOINT=http://otel-collector:4317
```

The collector image must be `opentelemetry-collector-contrib` (not the core image) because the `spanmetrics` connector is a contrib component.

---

## Developer Experience

```bash
docker compose up
# → open http://localhost:3000
# → LLM Observatory dashboard pre-loaded, no setup required
# → login: admin / admin
```

Point app at sidecar (unchanged from before):
```bash
export OPENAI_BASE_URL=http://localhost:4000/openai
```

Make LLM calls → tokens and latency appear in Grafana within ~15 seconds (Prometheus scrape interval).

---

## Testing

- Automated: existing 72 unit + integration tests unchanged (no sidecar code changes)
- Manual smoke test: `docker compose up` → make 3+ LLM calls → verify dashboard panels show data within 30 seconds
- Trace drill-down: click Explore link from dashboard → verify Tempo shows individual span with `gen_ai.*` attributes
