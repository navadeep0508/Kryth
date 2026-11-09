"""Secure credential storage using OS keyring, with plaintext TOML fallback.

Migrates away from storing plaintext API keys in configuration files.
Uses the OS native keychain (Windows Credential Manager, macOS Keychain,
Linux Secret Service) when available, falling back to a protected TOML file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import keyring
    import keyring.backends
    KEYRING_AVAILABLE = True
except ImportError:  # pragma: no cover - keyring may not be installed
    keyring = None  # type: ignore[assignment]
    KEYRING_AVAILABLE = False

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redefinition]

from platformdirs import user_config_dir

SERVICE_NAME = "kryth"
CREDENTIALS_FILE = "credentials.toml"


def _legacy_path() -> Path:
    return Path(user_config_dir("kryth")) / CREDENTIALS_FILE


def _kryth_path() -> Path:
    return Path(user_config_dir("kryth")) / CREDENTIALS_FILE


def _keyring_key(provider: str, field: str) -> str:
    return f"{provider}/{field}"


def save_credential(
    provider: str,
    field: str,
    value: str,
    use_keyring: bool = True,
) -> Path | None:
    """Save a credential value. Prefers OS keyring, falls back to protected TOML."""
    if use_keyring and KEYRING_AVAILABLE and keyring is not None:
        try:
            keyring.set_password(SERVICE_NAME, _keyring_key(provider, field), value)
            return None
        except Exception:
            pass  # fall through to TOML

    # Fallback: write to protected TOML file (0o600)
    path = _kryth_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import tomli_w  # type: ignore
    except ImportError:
        tomli_w = None  # type: ignore[assignment]

    if tomli_w is not None:
        data: dict[str, Any] = {}
        if path.is_file():
            try:
                with open(path, "rb") as f:
                    data = tomllib.load(f)
            except Exception:
                data = {}
        providers = data.setdefault("providers", {})
        provider_data = providers.setdefault(provider, {})
        provider_data[field] = value
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(tomli_w.dumps(data).encode())
        tmp.replace(path)
        os.chmod(path, 0o600)
        return path
    else:
        # Last resort: manual TOML writing
        lines: list[str] = []
        if path.is_file():
            try:
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            except Exception:
                lines = []
        # Simple append/replace for api_key and base_url fields
        key_line = f'{field} = "{value}"\n'
        section_header = f"[{provider}]\n"
        found_section = False
        found_key = False
        new_lines: list[str] = []
        in_section = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_section = stripped == section_header.strip()
                found_section = found_section or in_section
                new_lines.append(line)
                continue
            if in_section and stripped.startswith(f"{field} ="):
                new_lines.append(key_line)
                found_key = True
                continue
            new_lines.append(line)
        if not found_section:
            new_lines.append(section_header)
            new_lines.append(key_line)
        elif not found_key:
            new_lines.append(key_line)
        path.write_text("".join(new_lines), encoding="utf-8")
        os.chmod(path, 0o600)
        return path


def load_credential(
    provider: str,
    field: str,
    use_keyring: bool = True,
) -> str | None:
    """Load a credential value. Checks keyring first, then TOML fallback."""
    if use_keyring and KEYRING_AVAILABLE and keyring is not None:
        try:
            value = keyring.get_password(SERVICE_NAME, _keyring_key(provider, field))
            if value:
                return value
        except Exception:
            pass

    # Fallback: read from TOML file
    path = _kryth_path()
    if not path.is_file():
        # Also check legacy postal credentials path for migration
        legacy = _legacy_path()
        if legacy.is_file():
            try:
                with open(legacy, "rb") as f:
                    data = tomllib.load(f)
                if field == "api_key" and provider in ("openrouter", "postal", ""):
                    return data.get("api_key")
                if field == "base_url":
                    return data.get("base_url")
            except Exception:
                pass
        return None

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return None

    providers = data.get("providers", {})
    provider_data = providers.get(provider, {})
    return provider_data.get(field)


def clear_credential(provider: str, field: str, use_keyring: bool = True) -> bool:
    """Remove a credential from keyring or TOML."""
    removed = False
    if use_keyring and KEYRING_AVAILABLE and keyring is not None:
        try:
            keyring.delete_password(SERVICE_NAME, _keyring_key(provider, field))
            removed = True
        except Exception:
            pass

    path = _kryth_path()
    if path.is_file():
        try:
            import tomli_w  # type: ignore
        except ImportError:
            tomli_w = None  # type: ignore[assignment]

        with open(path, "rb") as f:
            data = tomllib.load(f)
        providers = data.get("providers", {})
        if provider in providers and field in providers[provider]:
            del providers[provider][field]
            if not providers[provider]:
                del providers[provider]
            if tomli_w is not None:
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_bytes(tomli_w.dumps(data).encode())
                tmp.replace(path)
                os.chmod(path, 0o600)
            removed = True
    return removed


def list_stored_providers() -> list[str]:
    """Return list of provider names that have stored credentials."""
    names: set[str] = set()

    if KEYRING_AVAILABLE and keyring is not None:
        try:
            for item in keyring.get_credential(SERVICE_NAME, ""):  # type: ignore[attr-defined]
                pass
        except Exception:
            pass
        # keyring doesn't have a clean list-all API; check known providers
        for known in ("openrouter", "openai", "anthropic", "google", "groq",
                      "nvidia", "deepseek", "mistral", "together", "fireworks",
                      "cerebras", "ollama", "lmstudio", "custom"):
            if load_credential(known, "api_key") or load_credential(known, "base_url"):
                names.add(known)

    path = _kryth_path()
    if path.is_file():
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            providers = data.get("providers", {})
            names.update(name for name in providers if name != "_kryth")
        except Exception:
            pass

    # Check legacy postal credentials
    legacy = _legacy_path()
    if legacy.is_file():
        try:
            with open(legacy, "rb") as f:
                data = tomllib.load(f)
            if data.get("api_key"):
                names.add("openrouter")
        except Exception:
            pass

    return sorted(names)


def migrate_legacy_credentials() -> dict[str, str]:
    """Migrate plaintext postal credentials.toml to keyring/Kryth storage.

    Returns a dict of migrated provider -> fields.
    """
    legacy = _legacy_path()
    migrated: dict[str, str] = {}
    if not legacy.is_file():
        return migrated

    try:
        with open(legacy, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return migrated

    api_key = data.get("api_key")
    base_url = data.get("base_url")

    if api_key:
        save_credential("openrouter", "api_key", api_key)
        migrated["openrouter"] = "api_key"
    if base_url:
        save_credential("openrouter", "base_url", base_url)
        migrated.setdefault("openrouter", "")
        migrated["openrouter"] += ", base_url"

    return migrated
