from __future__ import annotations

from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    sidecar_host: str = Field(default="0.0.0.0")
    sidecar_port: int = Field(default=4000)
    otlp_endpoint: str = Field(default="http://localhost:4317")
    openai_upstream: str = Field(default="https://api.openai.com")
    anthropic_upstream: str = Field(default="https://api.anthropic.com")
    log_level: str = Field(default="INFO")
    capture_prompts: bool = Field(default=False)

    @field_validator("openai_upstream", "anthropic_upstream")
    @classmethod
    def validate_upstream_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"Upstream URL must use http or https scheme, got: {v!r}"
            )
        if not parsed.netloc:
            raise ValueError(f"Upstream URL must include a host: {v!r}")
        return v.rstrip("/")


config = Settings()
