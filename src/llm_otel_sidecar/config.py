from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    sidecar_port: int = Field(default=4000)
    otlp_endpoint: str = Field(default="http://localhost:4317")
    openai_upstream: str = Field(default="https://api.openai.com")
    anthropic_upstream: str = Field(default="https://api.anthropic.com")
    log_level: str = Field(default="INFO")
    capture_prompts: bool = Field(default=False)


config = Settings()
