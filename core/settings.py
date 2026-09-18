import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Config(BaseSettings):
    # Directory
    base_dir: Path = Path(__file__).resolve().parent.parent
    cache_dir: Path = os.path.join(base_dir, "cache")

    model_config = SettingsConfigDict(
        env_file=base_dir / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def schema_cache_file(self, schema: str) -> Path:
        return self.cache_dir / f"{schema}.json"

    def business_cf_file(self, dimension: str) -> Path:
        return self.base_dir / "confirmed_business" / dimension

    # Database Connection
    db_host: str = Field(default="", validation_alias="DB_HOST")
    db_port: str = Field(default="", validation_alias="DB_PORT")
    db_name: str = Field(default="", validation_alias="DB_NAME")
    db_user: str = Field(default="", validation_alias="DB_USER")
    db_password: str = Field(default="", validation_alias="DB_PASSWORD")

    @property
    def db_result_file(self):
        return os.path.join(self.cache_dir, f"{self.db_name}.xlsx")

    @property
    def db_consistency_result_file(self):
        return os.path.join(self.cache_dir, f"{self.db_name}_consistency.xlsx")

    @property
    def db_uniqueness_result_file(self):
        return os.path.join(self.cache_dir, f"{self.db_name}_uniqueness.xlsx")


config = Config()
