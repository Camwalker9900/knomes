"""Application settings loaded from the environment (and optional .env file)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Knomes API."""

    # Read the repo-root .env first (works no matter the CWD), then a local
    # apps/api/.env override; real environment variables beat both.
    model_config = SettingsConfigDict(
        env_file=(str(Path(__file__).resolve().parents[3].parent / ".env"), ".env"),
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://knomes:knomes@localhost:5433/knomes"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "knomes-dev"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    repliers_api_key: str = ""
    admin_email: str = "admin@example.com"
    app_env: str = "development"
    app_secret: str = "dev-secret-change-me"
    hcad_download_url: str = ""  # set when licensing/URL confirmed
    houston_ckan_base_url: str = "https://data.houstontx.gov"
    houston_code_resource_id: str = ""


settings = Settings()
