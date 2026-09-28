"""Central configuration. Everything comes from environment variables / .env."""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # app
    app_env: str = "development"
    seed_demo_data: bool = True
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    data_dir: str = str(BASE_DIR / "data")

    # groq
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: float = 30.0

    # hindsight
    hindsight_api_url: str = ""
    hindsight_api_key: str = ""
    hindsight_bank_id: str = "college-infrastructure"
    hindsight_timeout_seconds: float = 15.0

    # github
    github_token: str = ""
    github_webhook_secret: str = ""
    github_repository: str = ""

    # deployment providers
    vercel_token: str = ""
    vercel_project_id: str = ""
    vercel_team_id: str = ""
    deployment_service_name: str = "Application"
    mock_deployments: bool = True
    correlation_window_minutes: int = 360

    @field_validator("groq_model", "hindsight_bank_id", "deployment_service_name", mode="before")
    @classmethod
    def _blank_uses_default(cls, v, info):
        # `GROQ_MODEL=` (blank) in .env should fall back to the default, not "".
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[info.field_name].default
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
