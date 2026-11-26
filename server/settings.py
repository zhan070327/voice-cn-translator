"""Deployment settings. Secrets have no defaults."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

REQUIRED = (
    "JWT_SECRET", "AZURE_SPEECH_KEY", "AZURE_SPEECH_RESOURCE", "AZURE_TRANSLATOR_KEY",
    "AZURE_TRANSLATOR_REGION", "PUBLIC_BASE_URL", "WECHAT_APP_ID", "WECHAT_MERCHANT_ID",
    "WECHAT_MERCHANT_SERIAL", "WECHAT_MERCHANT_PRIVATE_KEY", "WECHAT_API_V3_KEY",
    "WECHAT_PLATFORM_SERIAL", "WECHAT_PLATFORM_PUBLIC_KEY", "ALIPAY_APP_ID", "ALIPAY_SELLER_ID",
    "ALIPAY_APP_PRIVATE_KEY", "ALIPAY_PUBLIC_KEY",
)


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_cents: int


@dataclass(frozen=True)
class Settings:
    database: Path
    secrets: dict[str, str] = field(repr=False)
    plans: dict[str, Plan] = field(default_factory=dict)
    azure_cloud: str = "china"
    speech_url: str = ""
    speech_api_version: str = "2025-10-15"
    online_per_minute: int = 90
    global_per_minute: int = 600
    auth_per_minute: int = 12
    orders_per_minute: int = 5
    text_ms_per_character: int = 100
    access_seconds: int = 900
    refresh_seconds: int = 30 * 86400
    upstream_timeout: float = 1.6

    @classmethod
    def from_env(cls):
        values = {name: os.environ.get(name, "").strip() for name in REQUIRED}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError("Missing required environment variables: " + ", ".join(missing))
        if len(values["JWT_SECRET"].encode()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 bytes")
        if len(values["WECHAT_API_V3_KEY"].encode()) != 32:
            raise ValueError("WECHAT_API_V3_KEY must be exactly 32 bytes")
        domain = os.getenv("AZURE_CLOUD", "china")
        if domain not in {"china", "global"}:
            raise ValueError("AZURE_CLOUD must be china or global")
        for name in ("AZURE_SPEECH_RESOURCE", "AZURE_TRANSLATOR_REGION"):
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", values[name]):
                raise ValueError(f"Invalid {name}")
        if domain == "china" and values["AZURE_TRANSLATOR_REGION"] == "global":
            raise ValueError("AZURE_TRANSLATOR_REGION requires a China region")
        public_url = values["PUBLIC_BASE_URL"].rstrip("/")
        _https("PUBLIC_BASE_URL", public_url)
        values["PUBLIC_BASE_URL"] = public_url
        speech_url = os.getenv("AZURE_SPEECH_URL", "").strip()
        if speech_url:
            _https("AZURE_SPEECH_URL", speech_url)
        api_version = os.getenv("AZURE_SPEECH_API_VERSION", "2025-10-15")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-preview)?", api_version):
            raise ValueError("Invalid AZURE_SPEECH_API_VERSION")
        path = Path(os.getenv("PLANS_FILE", str(Path(__file__).with_name("plans.example.json"))))
        return cls(
            database=Path(os.getenv("DATABASE_PATH", "server/samefreq.sqlite3")),
            secrets=values,
            plans=_load_plans(path),
            azure_cloud=domain,
            speech_url=speech_url,
            speech_api_version=api_version,
            online_per_minute=_limit("ONLINE_REQUESTS_PER_MINUTE", 90),
            global_per_minute=_limit("GLOBAL_ONLINE_REQUESTS_PER_MINUTE", 600),
            auth_per_minute=_limit("AUTH_REQUESTS_PER_MINUTE", 12),
            orders_per_minute=_limit("ORDERS_PER_MINUTE", 5),
            text_ms_per_character=_limit("TRANSLATION_MS_PER_CHARACTER", 100),
        )


def _https(name, url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.query or parsed.fragment):
        raise ValueError(f"{name} must be a valid HTTPS URL")


def _limit(name, default):
    try:
        value = int(os.getenv(name, str(default)))
        if 0 < value <= 100_000:
            return value
    except ValueError:
        pass
    raise ValueError(f"{name} must be a positive integer up to 100000")


def _load_plans(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError
        plans = {}
        seen = set()
        for item in data:
            plan_id, name, price = item["id"], item["name"], item["price_cents"]
            if not re.fullmatch(r"[a-z0-9_-]{1,32}", plan_id) or not 0 < len(name.strip()) <= 64 or plan_id in seen:
                raise ValueError
            seen.add(plan_id)
            enabled = item["enabled"]
            if type(enabled) is not bool:
                raise ValueError
            if enabled:
                if type(price) is not int or not 0 < price <= 100_000_000:
                    raise ValueError
                plans[plan_id] = Plan(plan_id, name, price)
        return plans
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        raise ValueError("Invalid PLANS_FILE: enabled plans need a positive integer price_cents") from None
