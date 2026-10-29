"""配置中心：环境变量只放"启动必需"，可调参数全部进 app_config（架构 6.1）。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = "dev"
    data_dir: str = "./data"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://127.0.0.1:5173"]

    jwt_secret: str = "change-me-in-production"
    access_ttl_seconds: int = 900
    refresh_ttl_seconds: int = 2_592_000

    upload_max_mb: int = 50


@lru_cache
def get_settings() -> Settings:
    return Settings()
