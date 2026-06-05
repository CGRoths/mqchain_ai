from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MQCHAIN Intake Console"
    app_debug: bool = False
    api_prefix: str = "/api"
    database_url: str = "sqlite:///./data/mqchain_ai.db"
    source_upload_dir: str = "./data/source_uploads"
    staged_artifact_dir: str = "./data/staged_artifacts"
    source_upload_max_bytes: int = 25_000_000
    source_fetch_timeout_seconds: int = 10
    source_fetch_max_bytes: int = 10_000_000
    preview_candidate_limit: int = 100
    max_extraction_candidates_per_job: int = 10_000

    model_config = SettingsConfigDict(env_prefix="MQCHAIN_AI_", env_file=".env", extra="ignore")

    def ensure_data_dirs(self) -> None:
        for path in (self.source_upload_dir, self.staged_artifact_dir):
            Path(path).mkdir(parents=True, exist_ok=True)


settings = Settings()
