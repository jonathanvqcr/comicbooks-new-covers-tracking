from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import yaml

class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/comics.db"
    email_from: str = ""
    email_password: str = ""
    report_email: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

def load_watchlist() -> dict:
    """Load config/watchlist.yaml. Returns dict with 'series' and 'artists' lists."""
    watchlist_path = Path(__file__).parent.parent / "config" / "watchlist.yaml"
    if not watchlist_path.exists():
        return {"series": [], "artists": []}
    with open(watchlist_path) as f:
        return yaml.safe_load(f) or {"series": [], "artists": []}

settings = Settings()
