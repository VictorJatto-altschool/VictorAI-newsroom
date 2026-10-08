"""Load operator settings, watch list, voice guide and environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"


@dataclass
class SourceConfig:
    name: str
    kind: str
    tier: int
    category: str
    url: str = ""
    query: str = ""
    interval_minutes: int = 30
    enabled: bool = True
    x_handle: str = ""  # official X account; only these may be quote-posted automatically

    @property
    def key(self) -> str:
        return self.name.lower().replace(" ", "-")

    def feed_url(self) -> str:
        if self.kind == "gnews":
            from urllib.parse import quote_plus
            return f"https://news.google.com/rss/search?q={quote_plus(self.query)}&hl=en-US&gl=US&ceid=US:en"
        return self.url


@dataclass
class Env:
    app_env: str = "development"
    database_url: str = ""
    tz_name: str = "Africa/Lagos"
    gemini_api_key: str = ""
    groq_api_key: str = ""
    ai_base_url: str = ""  # any OpenAI-compatible endpoint (GitHub Models, OpenRouter, Mistral, Cerebras, Ollama)
    ai_api_key: str = ""
    ai_model: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_secret: str = ""
    youtube_api_key: str = ""
    nasa_api_key: str = ""

    @property
    def has_ai(self) -> bool:
        return bool(self.gemini_api_key or self.groq_api_key or self.ai_base_url)

    @property
    def dev_mode(self) -> bool:
        """Dev mode means the drafts are mock templates. With a real AI provider the output is real."""
        return self.app_env != "production" and not self.has_ai

    @property
    def has_x(self) -> bool:
        return all([self.x_api_key, self.x_api_secret, self.x_access_token, self.x_access_secret])

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


@dataclass
class Settings:
    raw: dict[str, Any]
    sources: list[SourceConfig]
    voice: str
    env: Env = field(default_factory=Env)

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    @property
    def automation(self) -> dict[str, Any]:
        return self.raw["automation"]

    @property
    def overnight(self) -> dict[str, Any]:
        return self.raw["overnight_rule"]

    @property
    def limits(self) -> dict[str, Any]:
        return self.raw["limits"]

    @property
    def scoring(self) -> dict[str, Any]:
        return self.raw["scoring"]

    @property
    def filter(self) -> dict[str, Any]:
        return self.raw["filter"]

    @property
    def drafting(self) -> dict[str, Any]:
        return self.raw["drafting"]


def load_env(dotenv_path: Path | None = None) -> Env:
    load_dotenv(dotenv_path or ROOT / ".env", override=False)
    g = os.environ.get
    return Env(
        app_env=g("APP_ENV", "development"),
        database_url=g("DATABASE_URL", ""),
        tz_name=g("TZ_NAME", "Africa/Lagos"),
        gemini_api_key=g("GEMINI_API_KEY", ""),
        groq_api_key=g("GROQ_API_KEY", ""),
        ai_base_url=g("AI_BASE_URL", ""),
        ai_api_key=g("AI_API_KEY", ""),
        ai_model=g("AI_MODEL", ""),
        telegram_bot_token=g("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=g("TELEGRAM_CHAT_ID", ""),
        x_api_key=g("X_API_KEY", ""),
        x_api_secret=g("X_API_SECRET", ""),
        x_access_token=g("X_ACCESS_TOKEN", ""),
        x_access_secret=g("X_ACCESS_SECRET", ""),
        youtube_api_key=g("YOUTUBE_API_KEY", ""),
        nasa_api_key=g("NASA_API_KEY", ""),
    )


def load_settings(config_dir: Path | None = None, env: Env | None = None) -> Settings:
    cdir = config_dir or CONFIG_DIR
    raw = yaml.safe_load((cdir / "settings.yaml").read_text(encoding="utf-8"))
    src_raw = yaml.safe_load((cdir / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    sources = [SourceConfig(**s) for s in src_raw]
    voice = (cdir / "voice.md").read_text(encoding="utf-8")
    return Settings(raw=raw, sources=sources, voice=voice, env=env or load_env())
