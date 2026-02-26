from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Trend2Biz API"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./trend2biz.db"
    github_token: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
