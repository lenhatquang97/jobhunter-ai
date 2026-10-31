from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JOBHUNTER_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://jobhunter:jobhunter@postgres/jobhunter"
    bridge_secret: SecretStr = SecretStr("")
    odoo_base_url: str = "http://odoo:8069"
    openai_api_key: SecretStr = SecretStr("")
    agent_adapters: str = ""
    model: str = "gpt-6.1-sol"
    reasoning_effort: str = "low"
    allow_sqlite: bool = False
    run_timeout_seconds: float = Field(default=120, gt=0, le=120)
    bridge_timeout_seconds: float = Field(default=15, gt=0, le=30)
    max_provider_requests: int = Field(default=6, ge=1, le=6)
    max_tool_calls: int = Field(default=8, ge=1, le=8)
    max_output_tokens: int = Field(default=8000, ge=256, le=16000)
    event_page_size: int = Field(default=200, ge=1, le=500)

    @model_validator(mode="after")
    def validate_runtime(self):
        if not self.database_url.startswith("postgresql+asyncpg://") and not (
            self.allow_sqlite and self.database_url.startswith("sqlite+aiosqlite://")
        ):
            raise ValueError("PostgreSQL is required outside explicitly enabled tests")
        parsed = urlsplit(self.odoo_base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("ODOO_BASE_URL must be a pinned http(s) origin")
        return self
