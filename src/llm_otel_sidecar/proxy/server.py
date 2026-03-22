from __future__ import annotations

from fastapi import FastAPI

from llm_otel_sidecar.proxy.openai import router as openai_router
from llm_otel_sidecar.proxy.anthropic import router as anthropic_router

app = FastAPI(title="llm-otel-sidecar")
app.include_router(openai_router)
app.include_router(anthropic_router)


# Health check
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
