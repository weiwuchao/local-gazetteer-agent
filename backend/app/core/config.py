"""配置中心：环境变量只放"启动必需"，可调参数全部进 app_config（架构 6.1）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py → parents[2] = backend/
BACKEND_ROOT = Path(__file__).resolve().parents[2]


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


def resolve_path(value: str | Path) -> Path:
    """把配置里的相对路径按 ``backend/`` 解析，而不是进程 cwd。

    与 ``repo.db.default_db_path()`` 保持同一口径：从仓库根目录启动 uvicorn 时，
    ``./data`` 仍指向 ``backend/data``，避免模型缓存与数据库落在两个地方。
    """
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (BACKEND_ROOT / path).resolve()
    return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
