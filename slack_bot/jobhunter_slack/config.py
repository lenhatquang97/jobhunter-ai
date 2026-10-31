from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOBHUNTER_SLACK_",
        env_file=".env.slack",
        extra="ignore",
    )
    bot_token: SecretStr = SecretStr("")
    app_token: SecretStr = SecretStr("")
    team_id: str = Field(pattern=r"^T[A-Z0-9]+$")
    hr_user_ids: list[str] = Field(min_length=1)
    lead_user_ids: list[str] = Field(min_length=1)
    database_url: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    model: str = "gpt-6.1-sol"
    allow_sqlite: bool = False
    poll_seconds: float = Field(default=2, ge=0.1, le=30)

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        import re

        if any(not re.fullmatch(r"[UW][A-Z0-9]+", uid) for uid in self.participants):
            raise ValueError("Configure Slack member IDs, not names or email addresses")
        url = self.database_url.get_secret_value()
        if not url.startswith("postgresql+asyncpg://") and not (
            self.allow_sqlite and url.startswith("sqlite+aiosqlite://")
        ):
            raise ValueError("A separate PostgreSQL database is required")
        return self

    @property
    def participants(self) -> set[str]:
        return set(self.hr_user_ids) | set(self.lead_user_ids)

    def permits(self, team_id: str, user_id: str) -> bool:
        return team_id == self.team_id and user_id in self.participants
