from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os
import json
import yaml
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False


@dataclass
class Settings:
    root: Path
    config: dict[str, Any]

    @property
    def timezone(self) -> str:
        return self.config.get("timezone", "Asia/Seoul")

    def path(self, dotted: str) -> Path:
        value: Any = self.config
        for part in dotted.split("."):
            value = value[part]
        p = Path(value)
        return p if p.is_absolute() else self.root / p

    def get(self, dotted: str, default: Any = None) -> Any:
        value: Any = self.config
        for part in dotted.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value


def load_settings(root: str | Path | None = None) -> Settings:
    root_path = Path(root or Path(__file__).resolve().parents[1]).resolve()
    load_dotenv(root_path / ".env")
    with open(root_path / "config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    free_env = os.getenv("FREE_MODE", "").strip().lower()
    if free_env:
        cfg["free_mode"] = free_env in {"1", "true", "yes", "y", "on"}
    # Personal/account-specific settings can stay in GitHub Secrets even when
    # the repository itself is public.
    positions_json = os.getenv("EXISTING_POSITIONS_JSON", "").strip()
    if positions_json:
        try:
            parsed = json.loads(positions_json)
            if isinstance(parsed, list):
                cfg["existing_positions"] = parsed
        except Exception as exc:
            raise ValueError(f"Invalid EXISTING_POSITIONS_JSON: {exc}") from exc
    capital = os.getenv("DAY_TRADE_CAPITAL_KRW", "").strip()
    if capital:
        cfg.setdefault("risk", {})["day_trade_capital_krw"] = int(float(capital))
    for dotted in ["storage.sqlite_path", "storage.token_cache_path", "storage.dart_corp_cache_path"]:
        path = Settings(root_path, cfg).path(dotted)
        path.parent.mkdir(parents=True, exist_ok=True)
    Settings(root_path, cfg).path("storage.output_dir").mkdir(parents=True, exist_ok=True)
    Settings(root_path, cfg).path("storage.model_dir").mkdir(parents=True, exist_ok=True)
    if Settings(root_path, cfg).get("storage.us_sqlite_path"):
        Settings(root_path, cfg).path("storage.us_sqlite_path").parent.mkdir(parents=True, exist_ok=True)
    if Settings(root_path, cfg).get("storage.us_model_dir"):
        Settings(root_path, cfg).path("storage.us_model_dir").mkdir(parents=True, exist_ok=True)
    return Settings(root_path, cfg)


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()
