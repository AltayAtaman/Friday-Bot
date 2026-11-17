"""Shared environment, path, and authorization configuration for Friday."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"


def _load_dotenv(path: Path = ENV_FILE) -> Dict[str, str]:
    """Parse simple KEY=VALUE entries from a local .env file."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


_DOTENV_CACHE = _load_dotenv()


def get_env_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve config/secret from process env, local .env, or Windows user registry."""
    val = os.environ.get(name) or _DOTENV_CACHE.get(name)
    if val:
        return val
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
            reg_val, _ = winreg.QueryValueEx(key, name)
            if reg_val:
                return str(reg_val)
        except Exception:
            pass
    return default


def get_owner_name() -> str:
    """Display name used in system prompts and greetings."""
    return get_env_secret("OWNER_NAME", "User") or "User"


def get_telegram_username() -> str:
    """Owner Telegram handle (without leading @) for HUD/logging."""
    return (get_env_secret("TELEGRAM_USERNAME", "user") or "user").lstrip("@")


def get_bot_username() -> str:
    """Bot Telegram handle (with leading @) for HUD display."""
    raw = get_env_secret("TELEGRAM_BOT_USERNAME", "@friday_remote_bot") or "@friday_remote_bot"
    return raw if raw.startswith("@") else f"@{raw}"


def get_repos_root() -> Path:
    """Root directory containing local git repositories."""
    configured = get_env_secret("FRIDAY_REPOS_ROOT")
    if configured:
        return Path(configured).expanduser()
    default_github = Path.home() / "Github"
    if default_github.is_dir():
        return default_github
    return Path.cwd()


def get_agy_exe() -> str:
    """Resolve the Antigravity CLI executable path."""
    configured = get_env_secret("AGY_EXE")
    if configured:
        return configured
    if found := shutil.which("agy"):
        return found
    return os.path.expandvars(r"%LOCALAPPDATA%\agy\bin\agy.exe")


def get_python_exe() -> str:
    """Resolve the Python interpreter path for spawned UI/viewer processes."""
    return get_env_secret("PYTHON_EXE") or sys.executable


def is_chat_authorized(
    chat_id: str,
    allowed_chat_id: Optional[str],
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Fail-closed authorization check requiring an explicit TELEGRAM_CHAT_ID."""
    if not allowed_chat_id:
        if logger:
            logger.warning("Rejected message: TELEGRAM_CHAT_ID is not configured (fail-closed).")
        return False
    if chat_id == str(allowed_chat_id):
        return True
    if logger:
        logger.warning(f"Unauthorized chat attempt: {chat_id}")
    return False
