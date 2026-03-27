from __future__ import annotations

import json
import logging
import posixpath
import time
from typing import Any, AsyncGenerator

import httpx
from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from llm_otel_sidecar.config import config
from llm_otel_sidecar.parsers.base import ParsedSpan
from llm_otel_sidecar.parsers.anthropic import parse_anthropic_response, PROVIDER as ANTHROPIC_PROVIDER
from llm_otel_sidecar.telemetry.emitter import emit_span
from llm_otel_sidecar.telemetry.conventions import UNKNOWN_MODEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/anthropic")

# Module-level singleton httpx client
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0),
    follow_redirects=False,
)

# Headers to forward from the client request to upstream
_FORWARD_REQUEST_HEADERS = {"authorization", "content-type", "anthropic-version", "x-api-key"}

# Headers to skip when returning upstream response to client
_SKIP_RESPONSE_HEADERS = {"content-encoding", "transfer-encoding", "content-length"}


def _build_upstream_headers(request: Request) -> dict[str, str]:
    """Extract forwarding headers from the incoming request."""
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in _FORWARD_REQUEST_HEADERS:
            headers[key] = value
    return headers


def _build_response_headers(upstream_headers: httpx.Headers) -> dict[str, str]:
    """Build response headers, skipping ones httpx/uvicorn manages."""
    headers: dict[str, str] = {}
    for key, value in upstream_headers.items():
        if key.lower() not in _SKIP_RESPONSE_HEADERS:
            headers[key] = value
    return headers


def _parse_sse_buffer(buffer: list[bytes]) -> dict[str, Any]:
    """Parse accumulated SSE chunks to reconstruct a response dict.

    Extracts model, usage, and stop_reason from Anthropic SSE stream events.
    Returns a dict suitable for parse_anthropic_response as response_body.

    Expected Anthropic SSE events:
    - message_start: contains model and input_tokens
    - message_delta: contains stop_reason and output_tokens
    - message_stop: signals end of stream
    """
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None

    raw = b"".join(buffer).decode("utf-8", errors="replace")
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data_str = line[len("data:"):].strip()
        try:
            chunk: dict[str, Any] = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        chunk_type = chunk.get("type")

        if chunk_type == "message_start":
            message: dict[str, Any] = chunk.get("message") or {}
            if model is None:
                model = message.get("model")
            usage: dict[str, Any] = message.get("usage") or {}
            if input_tokens is None:
                input_tokens = usage.get("input_tokens")

        elif chunk_type == "message_delta":
            delta: dict[str, Any] = chunk.get("delta") or {}
            if stop_reason is None:
                stop_reason = delta.get("stop_reason")
            usage_delta: dict[str, Any] = chunk.get("usage") or {}
            if output_tokens is None:
                output_tokens = usage_delta.get("output_tokens")

    response_dict: dict[str, Any] = {}
    if model is not None:
        response_dict["model"] = model
    usage_dict: dict[str, Any] = {}
    if input_tokens is not None:
        usage_dict["input_tokens"] = input_tokens
    if output_tokens is not None:
        usage_dict["output_tokens"] = output_tokens
    if usage_dict:
        response_dict["usage"] = usage_dict
    if stop_reason is not None:
        response_dict["stop_reason"] = stop_reason

    return response_dict


def _safe_path(path: str) -> str:
    """Collapse any .. segments to prevent path traversal."""
    return posixpath.normpath("/" + path).lstrip("/")


@router.post("/{path:path}")
async def proxy_anthropic(
    path: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Transparent proxy for all Anthropic POST endpoints."""
    upstream_url = config.anthropic_upstream + "/" + _safe_path(path)
    headers = _build_upstream_headers(request)

    # Read body once
    body_bytes = await request.body()

    try:
        request_dict: dict[str, Any] = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        request_dict = {}

    is_streaming: bool = request_dict.get("stream") is True

    if is_streaming:
        return await _handle_streaming(
            upstream_url=upstream_url,
            headers=headers,
            body_bytes=body_bytes,
            request_dict=request_dict,
        )
    else:
        return await _handle_non_streaming(
            upstream_url=upstream_url,
            headers=headers,
            body_bytes=body_bytes,
            request_dict=request_dict,
            background_tasks=background_tasks,
        )


async def _handle_non_streaming(
    upstream_url: str,
    headers: dict[str, str],
    body_bytes: bytes,
    request_dict: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> Response:
    """Forward a non-streaming request to upstream and return the response."""
    start_time = time.monotonic()
    try:
        upstream_response = await _client.post(
            upstream_url,
            content=body_bytes,
            headers=headers,
        )
    except httpx.TimeoutException:
        return Response(status_code=504, content=b"upstream timeout")

    latency_ms = (time.monotonic() - start_time) * 1000

    try:
        response_dict: dict[str, Any] = upstream_response.json()
    except Exception:
        response_dict = {}

    parsed = parse_anthropic_response(
        request_body=request_dict,
        response_body=response_dict,
        status_code=upstream_response.status_code,
        latency_ms=latency_ms,
        is_streaming=False,
    )
    background_tasks.add_task(emit_span, parsed)

    response_headers = _build_response_headers(upstream_response.headers)
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )


async def _handle_streaming(
    upstream_url: str,
    headers: dict[str, str],
    body_bytes: bytes,
    request_dict: dict[str, Any],
) -> StreamingResponse:
    """Forward a streaming request to upstream using SSE pass-through."""
    start_time = time.monotonic()

    # Mutable container so the generator can populate it and the background
    # task can read it after the stream ends.
    parsed_ref: list[ParsedSpan] = []

    # Send the request with streaming=True to get headers upfront before
    # constructing StreamingResponse, while keeping the body open for the
    # generator to consume.
    try:
        upstream_req = _client.build_request(
            "POST",
            upstream_url,
            content=body_bytes,
            headers=headers,
        )
        upstream_response = await _client.send(upstream_req, stream=True)
    except httpx.TimeoutException:
        logger.warning("Upstream timeout before streaming started")
        error_parsed = ParsedSpan(
            provider=ANTHROPIC_PROVIDER,
            request_model=request_dict.get("model", UNKNOWN_MODEL),
            response_model=request_dict.get("model", UNKNOWN_MODEL),
            latency_ms=(time.monotonic() - start_time) * 1000,
            status_code=504,
            is_streaming=True,
            error_type="timeout",
        )

        async def _empty_generator() -> AsyncGenerator[bytes, None]:
            return
            yield  # make it a generator

        async def _emit_error() -> None:
            emit_span(error_parsed)

        return StreamingResponse(
            _empty_generator(),
            status_code=504,
            media_type="text/event-stream",
            background=BackgroundTask(_emit_error),
        )

    upstream_status = upstream_response.status_code
    upstream_headers = _build_response_headers(upstream_response.headers)

    async def stream_generator() -> AsyncGenerator[bytes, None]:
        buffer: list[bytes] = []
        try:
            try:
                async for chunk in upstream_response.aiter_bytes():
                    buffer.append(chunk)
                    yield chunk
            except httpx.TimeoutException:
                logger.warning("Upstream timeout during streaming")
                error_parsed = ParsedSpan(
                    provider=ANTHROPIC_PROVIDER,
                    request_model=request_dict.get("model", UNKNOWN_MODEL),
                    response_model=request_dict.get("model", UNKNOWN_MODEL),
                    latency_ms=(time.monotonic() - start_time) * 1000,
                    status_code=504,
                    is_streaming=True,
                    error_type="timeout",
                )
                parsed_ref.append(error_parsed)
                return  # end the stream
        finally:
            await upstream_response.aclose()

        latency_ms = (time.monotonic() - start_time) * 1000
        response_dict = _parse_sse_buffer(buffer)

        parsed = parse_anthropic_response(
            request_body=request_dict,
            response_body=response_dict,
            status_code=upstream_status,
            latency_ms=latency_ms,
            is_streaming=True,
        )
        parsed_ref.append(parsed)

    async def emit_after_stream() -> None:
        if parsed_ref:
            emit_span(parsed_ref[0])
        else:
            logger.warning("No parsed span available after stream")

    return StreamingResponse(
        stream_generator(),
        status_code=upstream_status,
        media_type="text/event-stream",
        headers=upstream_headers,
        background=BackgroundTask(emit_after_stream),
    )
