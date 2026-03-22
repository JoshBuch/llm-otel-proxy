from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="llm-otel-sidecar")


# Health check
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# TODO: Register OpenAI router
# from llm_otel_sidecar.proxy.routes.openai import router as openai_router
# app.include_router(openai_router)

# TODO: Register Anthropic router
# from llm_otel_sidecar.proxy.routes.anthropic import router as anthropic_router
# app.include_router(anthropic_router)
