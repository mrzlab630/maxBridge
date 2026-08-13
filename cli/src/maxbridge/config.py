"""Configuration loader — merges default.yaml with local.yaml overrides.

Config search order: CLI path > ./config/local.yaml > ~/.config/maxbridge/config.yaml
> /etc/maxbridge/config.yaml > embedded default.yaml.
"""

import importlib.resources
import os
from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_default_config() -> dict[str, Any]:
    """Load default config from package data (works after pip install)."""
    ref = importlib.resources.files("maxbridge") / "data" / "default.yaml"
    content = ref.read_text(encoding="utf-8")
    return yaml.safe_load(content) or {}


def _find_local_config() -> Path | None:
    """Search standard locations for user config."""
    candidates = [
        Path.cwd() / "config" / "local.yaml",
        Path.home() / ".config" / "maxbridge" / "config.yaml",
        Path("/etc/maxbridge/config.yaml"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load configuration from YAML files.

    Priority: CLI arg > local config > embedded default > env vars.
    """
    config = _load_default_config()

    local_path = Path(config_path) if config_path else _find_local_config()
    if local_path and local_path.exists():
        with open(local_path, "r", encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        config = _deep_merge(config, local)

    _apply_env_overrides(config)
    return config


_ALLOWED_ENV_OVERRIDES = {
    "MAXBRIDGE_MAX_PHONE": ("max", "phone"),
    "MAXBRIDGE_LOGGING_LEVEL": ("logging", "level"),
    "MAXBRIDGE_DAEMON_PID_FILE": ("daemon", "pid_file"),
}


def _apply_env_overrides(config: dict) -> None:
    """Apply whitelisted MAXBRIDGE_* env vars as overrides."""
    for key, value in os.environ.items():
        parts = _ALLOWED_ENV_OVERRIDES.get(key)
        if parts is None:
            continue
        target = config
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        converted: Any = int(value) if value.lstrip("-").isdigit() else value
        target[parts[-1]] = converted


def get_nested(config: dict, path: str, default: Any = None) -> Any:
    """Get a nested config value by dot-path."""
    keys = path.split(".")
    current = config
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current
