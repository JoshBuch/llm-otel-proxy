from __future__ import annotations

import logging

import uvicorn

from llm_otel_sidecar.config import config
from llm_otel_sidecar.proxy.server import app
from llm_otel_sidecar.telemetry.emitter import init_tracer


def main() -> None:
    """Initialize the application and start the uvicorn server."""
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO)
    )
    init_tracer(config.otlp_endpoint)
    uvicorn.run(app, host="0.0.0.0", port=config.sidecar_port)


if __name__ == "__main__":
    main()
