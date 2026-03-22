from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from llm_otel_sidecar.config import config
from llm_otel_sidecar.parsers.base import ParsedSpan
from llm_otel_sidecar.parsers.openai import parse_openai_response
from llm_otel_sidecar.telemetry.emitter import emit_span

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/openai")

UNKNOWN_MODEL = "unknown"

# Module-level singleton httpx client
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0),
    follow_redirects=True,
)

# Headers to forward from the client request to upstream
_FORWARD_REQUEST_HEADERS = {"authorization", "content-type", "anthropic-version"}

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

    Extracts model, usage, and finish_reason from SSE stream data lines.
    Returns a dict suitable for parse_openai_response as response_body.
    """
    model: str | None = None
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None

    raw = b"".join(buffer).decode("utf-8", errors="replace")
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data_str = line[len("data:"):].strip()
        if data_str == "[DONE]":
            continue
        try:
            chunk: dict[str, Any] = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        if chunk.get("model") and model is None:
            model = chunk["model"]

        if chunk.get("usage"):
            usage = chunk["usage"]

        choices: list[Any] = chunk.get("choices") or []
        if choices and choices[0].get("finish_reason") is not None:
            finish_reason = choices[0]["finish_reason"]

    response_dict: dict[str, Any] = {}
    if model is not None:
        response_dict["model"] = model
    if usage is not None:
        response_dict["usage"] = usage
    if finish_reason is not None:
        response_dict["choices"] = [{"finish_reason": finish_reason}]
    else:
        response_dict["choices"] = []

    return response_dict


@router.post("/{path:path}")
async def proxy_openai(
    path: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Transparent proxy for all OpenAI POST endpoints."""
    upstream_url = config.openai_upstream.rstrip("/") + "/" + path
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

    parsed = parse_openai_response(
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
    # Inject stream_options to include usage in the last chunk
    modified_request = dict(request_dict)
    existing_stream_options: dict[str, Any] = dict(modified_request.get("stream_options") or {})
    existing_stream_options["include_usage"] = True
    modified_request["stream_options"] = existing_stream_options
    modified_body = json.dumps(modified_request).encode("utf-8")

    # Ensure Content-Type is set for the modified body
    modified_headers = dict(headers)
    modified_headers["content-type"] = "application/json"

    start_time = time.monotonic()

    # Mutable container so the generator can populate it and the background
    # task can read it after the stream ends.
    parsed_ref: list[ParsedSpan] = []

    async def stream_generator() -> AsyncIterator[bytes]:
        buffer: list[bytes] = []
        upstream_status: list[int] = []

        try:
            async with _client.stream(
                "POST",
                upstream_url,
                content=modified_body,
                headers=modified_headers,
            ) as upstream_response:
                upstream_status.append(upstream_response.status_code)
                async for chunk in upstream_response.aiter_bytes():
                    buffer.append(chunk)
                    yield chunk
        except httpx.TimeoutException:
            logger.warning("Upstream timeout during streaming")
            error_parsed = ParsedSpan(
                provider="openai",
                model=request_dict.get("model", UNKNOWN_MODEL),
                latency_ms=(time.monotonic() - start_time) * 1000,
                status_code=504,
                is_streaming=True,
                error_type="timeout",
            )
            parsed_ref.append(error_parsed)
            return  # end the stream

        latency_ms = (time.monotonic() - start_time) * 1000
        response_dict = _parse_sse_buffer(buffer)
        status_code = upstream_status[0] if upstream_status else 200

        parsed = parse_openai_response(
            request_body=request_dict,
            response_body=response_dict,
            status_code=status_code,
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
        media_type="text/event-stream",
        background=BackgroundTask(emit_after_stream),
    )
