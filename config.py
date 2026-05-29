import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_email: str
    from_name: str
    send_delay_seconds: int = 30


@dataclass
class Config:
    anthropic_api_key: str
    smtp: SmtpConfig
    db_path: str = "data/companies.db"
    google_places_api_key: str = ""
    tracking_url: str = "http://localhost:8000"


def load_config() -> Config:
    smtp = SmtpConfig(
        host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        port=int(os.environ.get("SMTP_PORT", "587")),
        user=os.environ.get("SMTP_USER", ""),
        password=os.environ.get("SMTP_PASSWORD", ""),
        from_email=os.environ.get("FROM_EMAIL", ""),
        from_name=os.environ.get("FROM_NAME", "Web Dev Agency"),
        send_delay_seconds=int(os.environ.get("SEND_DELAY_SECONDS", "30")),
    )
    return Config(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        smtp=smtp,
        db_path=os.environ.get("DB_PATH", "data/companies.db"),
        google_places_api_key=os.environ.get("GOOGLE_PLACES_API_KEY", ""),
        tracking_url=os.environ.get("TRACKING_URL", "http://localhost:8000"),
    )
