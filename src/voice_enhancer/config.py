from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    bot_token: str = ""
    max_bot_token: str = ""
    max_api_base_url: str = "https://platform-api2.max.ru"
    max_webhook_secret: str = ""
    max_ca_bundle: str | None = None
    # Explicit CDN allowlist; never download arbitrary URLs from message bodies.
    max_media_hosts: str = "max.ru,okcdn.ru,mycdn.me,userapi.com"
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    database_url: str = "postgresql+asyncpg://voice:voice@postgres:5432/voice"
    redis_url: str = "redis://redis:6379/0"
    media_root: str = "/data/media"
    telegram_api_base_url: str | None = None
    enhancement_provider: str = "ffmpeg"
    elevenlabs_api_key: str = ""
    glm_api_key: str = ""
    gtcrn_model: str = "/opt/models/gtcrn_simple.onnx"
    deepfilter_bin: str = "deep-filter"
    max_media_duration_seconds: int = 600
    max_media_size_bytes: int = 2 * 1024**3
    max_active_jobs_per_user: int = Field(default=3, ge=1, le=100)
    max_active_jobs: int = Field(default=100, ge=1, le=10000)
    max_concurrent_uploads: int = Field(default=2, ge=1, le=16)
    min_free_disk_bytes: int = Field(default=1024**3, ge=0)
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    ffmpeg_timeout_seconds: int = 1800


settings = Settings()
