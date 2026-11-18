from dataclasses import dataclass


@dataclass
class Settings:
    """Application configuration defaults."""

    database_path: str = "shortener.db"
    key_length: int = 7
    validation_timeout: float = 5.0
    base_url: str = "http://localhost:8000"
