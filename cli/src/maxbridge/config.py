"""Configuration loader — merges default.yaml with local.yaml overrides.

Config search order: CLI path > ./config/local.yaml > ~/.config/maxbridge/config.yaml
> /etc/maxbridge/config.yaml > embedded default.yaml.
"""

import importlib.resources
import os
from pathlib import Path
from typing import Any

import yaml

_SYSTEM_CONFIG_PATH = Path("/etc/maxbridge/config.yaml")
_SYSTEM_STATE_ROOT = Path("/var/lib/maxbridge")


class ResolvedConfig(dict[str, Any]):
    """Configuration values plus the canonical source and runtime paths."""

    def __init__(
        self,
        values: dict[str, Any],
        *,
        source_path: Path | None,
        runtime_root: Path,
    ) -> None:
        super().__init__(values)
        self.source_path = source_path
        self.runtime_root = runtime_root
        self.telegram_config_path = runtime_root / "data" / "telegram.json"


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


def _canonical_path(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def resolve_config_path(
    config_path: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Return the canonical explicit or discovered primary config path."""
    selected = Path(config_path) if config_path is not None else _find_local_config()
    return _canonical_path(selected) if selected is not None else None


def runtime_root_for_config(config_path: Path | None) -> Path:
    """Map a primary config path to its deterministic writable state root."""
    if config_path is None:
        return _canonical_path(Path.cwd())
    if config_path == _SYSTEM_CONFIG_PATH:
        return _SYSTEM_STATE_ROOT
    if config_path.name == "local.yaml" and config_path.parent.name == "config":
        return config_path.parent.parent
    return config_path.parent


def load_config(
    config_path: str | os.PathLike[str] | None = None,
) -> ResolvedConfig:
    """Load configuration from YAML files.

    Priority: CLI arg > local config > embedded default > env vars.
    """
    config = _load_default_config()

    local_path = resolve_config_path(config_path)
    if local_path and local_path.exists():
        with open(local_path, "r", encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        config = _deep_merge(config, local)

    _apply_env_overrides(config)
    return ResolvedConfig(
        config,
        source_path=local_path,
        runtime_root=runtime_root_for_config(local_path),
    )


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
