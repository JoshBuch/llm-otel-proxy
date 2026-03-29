# Contributing to llm-otel-proxy

Thanks for your interest. The codebase is intentionally small and focused — contributions that add complexity without clear value will be declined, but improvements to correctness, provider support, and observability coverage are very welcome.

## What we want

- Bug fixes with a failing test that demonstrates the bug
- New provider support (Azure OpenAI, Bedrock, Vertex AI — see roadmap)
- Improvements to OTel GenAI semconv coverage
- Documentation improvements

## What we don't want

- Abstraction layers that don't simplify real use cases
- Dependencies beyond what's already in `pyproject.toml` without strong justification
- Breaking changes to the proxy passthrough behaviour

---

## Getting started

```bash
git clone https://github.com/JoshBuch/llm-otel-proxy.git
cd llm-otel-proxy
pip install -e ".[dev]"
```

Run the tests to confirm everything is working:

```bash
pytest tests/unit/       # fast, no I/O
pytest tests/integration/ # httpx mocks, no real API calls
```

Run type checking:

```bash
mypy src/llm_otel_sidecar/
```

---

## Project structure

```
src/llm_otel_sidecar/
├── config.py            # All env var settings via pydantic-settings
├── proxy/
│   ├── server.py        # FastAPI app, mounts routers
│   ├── openai.py        # /openai/* handler (streaming + non-streaming)
│   └── anthropic.py     # /anthropic/* handler
├── parsers/
│   ├── base.py          # ParsedSpan dataclass — contract between parsers and emitter
│   ├── openai.py        # Extract OTel GenAI semconv fields from OpenAI response
│   └── anthropic.py     # Same for Anthropic
└── telemetry/
    ├── conventions.py   # OTel attribute name constants (never inline these strings)
    └── emitter.py       # Build and export OTel spans
```

## Key invariants (don't break these)

- **Streaming is never buffered before forwarding.** Yield chunks immediately; accumulate a side-copy for telemetry only after the stream ends.
- **Telemetry is fire-and-forget.** Use `background_tasks.add_task(...)`. Span emission must never delay or fail the response.
- **Byte-for-byte passthrough.** Status code, headers, and body must be identical to what the upstream returned.
- **No state, no storage.** Stateless proxy only. Persistence belongs in the OTel backend.

---

## Adding a new provider

1. Add a new `parsers/<provider>.py` that returns a `ParsedSpan`
2. Add a new `proxy/<provider>.py` with a FastAPI router
3. Mount the router in `proxy/server.py`
4. Add the upstream URL to `config.py`
5. Write unit tests for the parser and integration tests for the proxy handler

The OpenAI and Anthropic implementations are the reference — follow the same pattern.

---

## Coding standards

- `async def` everywhere — no `requests`, only `httpx.AsyncClient`
- Full type annotations on every function; `from __future__ import annotations` at the top of every file
- Config values from `config.py` only — no inline URLs or ports
- OTel attribute strings only from `telemetry/conventions.py` constants — never inline them

---

## Submitting a PR

1. Fork the repo and create a branch from `main`
2. Make your changes with tests
3. Ensure `pytest` and `mypy src/llm_otel_sidecar/` both pass
4. Open a PR with a clear description of what it does and why

CI runs automatically on every PR. PRs with failing tests or type errors won't be merged.
