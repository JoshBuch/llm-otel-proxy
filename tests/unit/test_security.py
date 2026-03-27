from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_otel_sidecar.proxy.anthropic import _safe_path as anthropic_safe_path
from llm_otel_sidecar.proxy.anthropic import _client as anthropic_client
from llm_otel_sidecar.proxy.openai import _safe_path as openai_safe_path
from llm_otel_sidecar.proxy.openai import _client as openai_client


# ---------------------------------------------------------------------------
# 1. sidecar_host default must be 0.0.0.0 for Docker port-mapping to work
#
# Rationale: the sidecar runs inside a Docker container. Docker port publishing
# (ports: "127.0.0.1:4000:4000") only reaches the container if the process
# inside binds to 0.0.0.0. The host-side 127.0.0.1 scoping in docker-compose
# is what limits external access, not the in-container bind address.
# ---------------------------------------------------------------------------


def test_sidecar_host_default_is_all_interfaces() -> None:
    """sidecar_host must default to 0.0.0.0 so Docker port mapping works."""
    from llm_otel_sidecar.config import Settings

    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        openai_upstream="https://api.openai.com",
        anthropic_upstream="https://api.anthropic.com",
    )
    assert s.sidecar_host == "0.0.0.0"


# ---------------------------------------------------------------------------
# 2. Upstream URL validator — scheme enforcement
# ---------------------------------------------------------------------------


def test_upstream_url_validator_rejects_ftp_scheme() -> None:
    """Upstream URLs with non-http(s) schemes must be rejected at config load."""
    from pydantic import ValidationError
    from llm_otel_sidecar.config import Settings

    with pytest.raises(ValidationError, match="http or https"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            openai_upstream="ftp://evil.com",
            anthropic_upstream="https://api.anthropic.com",
        )


def test_upstream_url_validator_rejects_missing_host() -> None:
    """Upstream URLs without a host must be rejected."""
    from pydantic import ValidationError
    from llm_otel_sidecar.config import Settings

    with pytest.raises(ValidationError, match="host"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            openai_upstream="https://",
            anthropic_upstream="https://api.anthropic.com",
        )


def test_upstream_url_validator_accepts_https_url() -> None:
    """Standard https upstream URLs must be accepted without error."""
    from llm_otel_sidecar.config import Settings

    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        openai_upstream="https://api.openai.com",
        anthropic_upstream="https://api.anthropic.com",
    )
    assert s.openai_upstream == "https://api.openai.com"
    assert s.anthropic_upstream == "https://api.anthropic.com"


def test_upstream_url_validator_accepts_http_localhost() -> None:
    """http://localhost is valid for local dev/test environments."""
    from llm_otel_sidecar.config import Settings

    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        openai_upstream="http://localhost:8080",
        anthropic_upstream="http://localhost:8081",
    )
    assert s.openai_upstream == "http://localhost:8080"


def test_upstream_url_validator_strips_trailing_slash() -> None:
    """Trailing slashes must be stripped so URL construction doesn't double-slash."""
    from llm_otel_sidecar.config import Settings

    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        openai_upstream="https://api.openai.com/",
        anthropic_upstream="https://api.anthropic.com/",
    )
    assert not s.openai_upstream.endswith("/")
    assert not s.anthropic_upstream.endswith("/")


# ---------------------------------------------------------------------------
# 3. Path sanitization — _safe_path collapses traversal sequences
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,expected", [
    ("v1/chat/completions", "v1/chat/completions"),
    ("v1/messages", "v1/messages"),
    ("v1/embeddings", "v1/embeddings"),
    # traversal attempts
    ("../../etc/passwd", "etc/passwd"),
    ("v1/../../etc/passwd", "etc/passwd"),
    ("../v1/chat/completions", "v1/chat/completions"),
    # redundant slashes / dots
    ("v1/./chat/completions", "v1/chat/completions"),
    ("v1//chat//completions", "v1/chat/completions"),
])
def test_openai_safe_path_normalizes_correctly(path: str, expected: str) -> None:
    """_safe_path must collapse traversal sequences and normalize the path."""
    assert openai_safe_path(path) == expected


@pytest.mark.parametrize("path,expected", [
    ("v1/messages", "v1/messages"),
    ("../../etc/passwd", "etc/passwd"),
    ("v1/../../etc/passwd", "etc/passwd"),
])
def test_anthropic_safe_path_normalizes_correctly(path: str, expected: str) -> None:
    """Anthropic _safe_path must normalize paths identically to OpenAI."""
    assert anthropic_safe_path(path) == expected


# ---------------------------------------------------------------------------
# 4. follow_redirects must be False on both httpx clients
#
# Rationale: OpenAI and Anthropic don't redirect API endpoints. Allowing
# redirects enables SSRF chains where a redirect on the upstream host routes
# traffic to an internal address, carrying the Authorization header.
# ---------------------------------------------------------------------------


def test_openai_client_does_not_follow_redirects() -> None:
    """The OpenAI httpx client must have follow_redirects=False."""
    assert openai_client.follow_redirects is False


def test_anthropic_client_does_not_follow_redirects() -> None:
    """The Anthropic httpx client must have follow_redirects=False."""
    assert anthropic_client.follow_redirects is False


# ---------------------------------------------------------------------------
# 5. OTLP TLS — insecure flag derived from endpoint URL scheme
# ---------------------------------------------------------------------------


def test_init_tracer_http_endpoint_sets_insecure_true() -> None:
    """init_tracer with an http:// endpoint must pass insecure=True to OTLPSpanExporter."""
    from llm_otel_sidecar.telemetry.emitter import init_tracer

    with patch("llm_otel_sidecar.telemetry.emitter.OTLPSpanExporter") as mock_exporter:
        mock_exporter.return_value = MagicMock()
        init_tracer("http://localhost:4317")

    mock_exporter.assert_called_once()
    _, kwargs = mock_exporter.call_args
    assert kwargs.get("insecure") is True, (
        "http:// endpoint must use insecure=True (plaintext gRPC)"
    )


def test_init_tracer_https_endpoint_sets_insecure_false() -> None:
    """init_tracer with an https:// endpoint must pass insecure=False to OTLPSpanExporter."""
    from llm_otel_sidecar.telemetry.emitter import init_tracer

    with patch("llm_otel_sidecar.telemetry.emitter.OTLPSpanExporter") as mock_exporter:
        mock_exporter.return_value = MagicMock()
        init_tracer("https://otel.example.com:4317")

    mock_exporter.assert_called_once()
    _, kwargs = mock_exporter.call_args
    assert kwargs.get("insecure") is False, (
        "https:// endpoint must use insecure=False (TLS gRPC)"
    )
