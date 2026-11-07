from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["local", "remote"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Agent
    agent_id: str = "team-08-news"
    group_id: str = "team-08"
    agent_version: str = "0.1.0"
    agent_public_endpoint: str = "http://localhost:8008"

    # Hạ tầng (ports & adapters)
    registry_mode: Mode = "local"
    registry_url: str = ""
    local_registry_file: str = "local_registry.yaml"
    broker_mode: Mode = "local"
    broker_url: str = ""
    identity_mode: Mode = "local"
    identity_url: str = ""
    identity_client_secret: str = ""
    local_shared_secret: str = "dev-shared-secret"
    tracer_mode: Mode = "local"
    tracer_url: str = ""

    # Data
    database_url: str = "postgresql+asyncpg://news:news@localhost:5432/news"
    redis_url: str = "redis://localhost:6379/0"

    # AI. Everything works without a key, through non-LLM fallbacks.
    gemini_api_key: str = ""
    llm_model: str = "gemini-3.5-flash-lite"
    # Changing the embedding model requires re-embedding every article.
    embedding_model: str = "gemini-embedding-2"
    # Both must fit inside capability_timeout_s, so that a slow model ends in a fallback rather than a timeout.
    llm_timeout_s: float = 4.5
    query_embed_timeout_s: float = 1.5
    # Pause between enricher batches, to stay within API rate limits.
    enrich_interval_s: float = 10.0

    # Agent protocol
    capability_timeout_s: float = 8.0
    dedup_ttl_s: int = 600
    heartbeat_interval_s: float = 15.0

    # Pipeline
    crawl_interval_s: int = 600
    crawler_stale_after_s: int = 1800
    http_user_agent: str = "NewsAgent/0.1 (+MI4304 student project)"

    # Dashboard
    admin_password: str = "change-me"
    cors_origins: str = "http://localhost:3000"

    # Dev
    faults_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
