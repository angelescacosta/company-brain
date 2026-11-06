"""Required Open WebUI privacy settings (T4.AC1).

The launcher refuses to start Open WebUI if `config/openwebui.env` is missing
a required setting or has a different value for it.
"""

from __future__ import annotations

from pathlib import Path

ENV_FILE = Path("config") / "openwebui.env"

_FALSE = "false"
REQUIRED: dict[str, str] = {
    "HOST": "127.0.0.1",
    "PORT": "8080",
    "OFFLINE_MODE": "true",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "ENABLE_PERSISTENT_CONFIG": _FALSE,
    "SCARF_NO_ANALYTICS": "true",
    "DO_NOT_TRACK": "true",
    "ANONYMIZED_TELEMETRY": _FALSE,
    "ENABLE_OTEL": _FALSE,
    "ENABLE_VERSION_UPDATE_CHECK": _FALSE,
    "ENABLE_COMMUNITY_SHARING": _FALSE,
    "ENABLE_TITLE_GENERATION": _FALSE,
    "ENABLE_TAGS_GENERATION": _FALSE,
    "ENABLE_FOLLOW_UP_GENERATION": _FALSE,
    "ENABLE_AUTOCOMPLETE_GENERATION": _FALSE,
    "ENABLE_RETRIEVAL_QUERY_GENERATION": _FALSE,
    "ENABLE_SEARCH_QUERY_GENERATION": _FALSE,
    "ENABLE_WEB_SEARCH": _FALSE,
    "ENABLE_CODE_EXECUTION": _FALSE,
    "ENABLE_CODE_INTERPRETER": _FALSE,
    "ENABLE_IMAGE_GENERATION": _FALSE,
    "ENABLE_DIRECT_CONNECTIONS": _FALSE,
    "ENABLE_OPENAI_API": _FALSE,
    "ENABLE_OLLAMA_API": _FALSE,
    "ENABLE_EVALUATION_ARENA_MODELS": _FALSE,
    "ENABLE_GOOGLE_DRIVE_INTEGRATION": _FALSE,
    "ENABLE_ONEDRIVE_INTEGRATION": _FALSE,
    "ENABLE_SIGNUP": _FALSE,
    "ENABLE_OAUTH_SIGNUP": _FALSE,
    "ENABLE_LDAP": _FALSE,
    "ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS": _FALSE,
    "ENABLE_FORWARD_USER_INFO_HEADERS": _FALSE,
    "USER_PERMISSIONS_CHAT_FILE_UPLOAD": _FALSE,
    "RAG_EMBEDDING_MODEL_AUTO_UPDATE": _FALSE,
    "AUDIT_LOG_LEVEL": "NONE",
    "ENABLE_AUDIT_LOGS_FILE": _FALSE,
    "WEBUI_AUTH": "true",
    "CORS_ALLOW_ORIGIN": "http://127.0.0.1:8080",
}


def parse(text: str) -> tuple[dict[str, str], set[str]]:
    """Return the settings (last value wins, as Open WebUI reads them) and duplicate keys."""
    values: dict[str, str] = {}
    duplicates: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        value = value.strip("\"'")
        if key in values:
            duplicates.add(key)
        values[key] = value
    return values, duplicates


def problems(text: str) -> list[str]:
    values, duplicates = parse(text)
    found = [f"{key} appears more than once" for key in sorted(duplicates & set(REQUIRED))]
    for key, expected in REQUIRED.items():
        if key not in values:
            found.append(f"{key} is missing (must be {expected})")
        elif values[key] != expected:
            found.append(f"{key} is {values[key]!r} (must be {expected})")
    return found
