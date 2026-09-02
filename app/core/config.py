from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"

    database_url: str = "postgresql://postgres:postgres@localhost:5432/payments"
    redis_url: str = "redis://localhost:6379/0"

    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    provider_url: str = "http://localhost:8001"
    provider_api_key: str = "provider-development-key"

    merchant_api_key: str = "merchant-development-key"
    webhook_secret: str = "development-webhook-secret"

    idempotency_lock_seconds: int = 30
    idempotency_result_seconds: int = 86400

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()