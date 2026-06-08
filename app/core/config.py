from __future__ import annotations

from pathlib import Path

from pydantic import Field
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
    github_crawl_max_depth: int = 3
    github_crawl_max_files: int = 100
    github_crawl_max_bytes_per_file: int = 1_000_000
    github_crawl_include_migrations: bool = False
    github_crawl_max_networks: int = 50
    github_crawl_max_markets_per_network: int = 50
    github_crawl_max_priority_files_per_market: int = 8
    github_api_token: str | None = Field(default=None, validation_alias="MQCHAIN_GITHUB_API_TOKEN")
    preview_candidate_limit: int = 100
    max_extraction_candidates_per_job: int = 10_000
    pdf_max_pages: int | None = 50

    model_config = SettingsConfigDict(env_prefix="MQCHAIN_AI_", env_file=".env", extra="ignore")

    def ensure_data_dirs(self) -> None:
        for path in (self.source_upload_dir, self.staged_artifact_dir):
            Path(path).mkdir(parents=True, exist_ok=True)


settings = Settings()
