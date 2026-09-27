from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    bot_token: str = ""
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    database_url: str = "postgresql+asyncpg://voice:voice@postgres:5432/voice"
    redis_url: str = "redis://redis:6379/0"
    media_root: str = "/data/media"
    telegram_api_base_url: str | None = None
    enhancement_provider: str = "ffmpeg"
    elevenlabs_api_key: str = ""
    glm_api_key: str = ""
    deepfilter_bin: str = "deep-filter"
    max_media_duration_seconds: int = 600
    max_media_size_bytes: int = 2 * 1024**3
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    ffmpeg_timeout_seconds: int = 1800


settings = Settings()
