"""Firewall and loopback checks (T4).

The verdict rule: a probe counts only when its control (the owner's `curl` to
the same address family) reaches the internet. An applicable probe passes only
when the blocked program did not connect and LuLu recorded the denial. Any
connection is a failure; anything else, including no applicable probe at all,
is inconclusive and never a pass.
"""

from __future__ import annotations

import plistlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# Service name: (port, expected command name prefix in lsof).
SERVICE_PORTS: dict[str, tuple[int, str]] = {
    "ollama": (11434, "ollama"),
    "postgresql": (5432, "postgres"),
    "openwebui": (8080, "python3"),
}


@dataclass(frozen=True)
class ProbeResult:
    name: str
    control_ok: bool
    connected: bool
    deny_recorded: bool


def probe_outcome(probe: ProbeResult) -> str:
    if not probe.control_ok:
        return "not applicable"
    if probe.connected:
        return "fail"
    return "pass" if probe.deny_recorded else "inconclusive"


def verdict(probes: Sequence[ProbeResult]) -> str:
    outcomes = [probe_outcome(p) for p in probes]
    if "fail" in outcomes:
        return "fail"
    applicable = [o for o in outcomes if o != "not applicable"]
    if not applicable or "inconclusive" in applicable:
        return "inconclusive"
    return "pass"


_LSOF_LINE = re.compile(
    r"^(?P<command>\S+)\s+(?P<pid>\d+)\s+.*\sTCP\s+(?P<address>\[?[^\s\]]*\]?):(?P<port>\d+)\s+\(LISTEN\)$"
)


def listeners(lsof_output: str) -> list[tuple[str, int, str, int]]:
    """Parse `lsof -nP -iTCP -sTCP:LISTEN` into (command, pid, address, port)."""
    found = []
    for line in lsof_output.splitlines()[1:]:
        match = _LSOF_LINE.match(line.strip())
        if match:
            address = match["address"].strip("[]")
            found.append((match["command"], int(match["pid"]), address, int(match["port"])))
    return found


# LuLu's rule list and settings (readable by everyone, written only by LuLu).
LULU_RULES = Path("/Library/Objective-See/LuLu/rules.plist")
LULU_PREFERENCES = Path("/Library/Objective-See/LuLu/preferences.plist")
BLOCK, ALLOW = 0, 1
# No blanket rule may let these programs through, and LuLu must be filtering.
PINNED_SETTINGS = {
    "allowApple": False,
    "allowInstalled": False,
    "passiveMode": False,
    "disabled": False,
}
_MAX_DEPTH = 64


def decode_keyed_archive(data: bytes, top: str = "root") -> object:
    """Decode an NSKeyedArchiver property list into plain dicts, lists and values."""
    archive = plistlib.loads(data)
    objects = archive["$objects"]

    def key(value: object) -> object:
        return value if isinstance(value, (str, int, float, bool, type(None))) else repr(value)

    def resolve(value: object, depth: int) -> object:
        if depth > _MAX_DEPTH:
            raise ValueError("archive nested too deeply")
        if isinstance(value, plistlib.UID):
            value = objects[value.data]
        if value == "$null":
            return None
        if isinstance(value, dict):
            if "NS.keys" in value:
                return {
                    key(resolve(k, depth + 1)): resolve(v, depth + 1)
                    for k, v in zip(value["NS.keys"], value["NS.objects"], strict=True)
                }
            if "NS.objects" in value:
                return [resolve(v, depth + 1) for v in value["NS.objects"]]
            if "NS.string" in value:
                return value["NS.string"]
            return {k: resolve(v, depth + 1) for k, v in value.items() if k != "$class"}
        return value

    return resolve(archive["$top"][top], 0)


# macOS keeps network filter switches here; LuLu filters only while its own is on.
NETWORK_EXTENSIONS = Path("/Library/Preferences/com.apple.networkextension.plist")
LULU_PROVIDER = "com.objective-see.lulu.extension"


def lulu_filter_enabled(data: bytes) -> bool:
    """True when macOS has LuLu's network filter switched on."""
    tops = plistlib.loads(data)["$top"]
    for name in tops:
        try:
            config = decode_keyed_archive(data, name)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        content_filter = config.get("ContentFilter") if isinstance(config, dict) else None
        if isinstance(content_filter, dict):
            provider = content_filter.get("Provider") or {}
            if (
                isinstance(provider, dict)
                and provider.get("DataProviderBundleIdentifier") == LULU_PROVIDER
                and content_filter.get("Enabled") is True
            ):
                return True
    return False


@dataclass(frozen=True)
class LuluRule:
    path: str
    action: int
    address: str
    port: str
    disabled: bool
    authority: str | None


def lulu_rules(data: bytes) -> list[LuluRule]:
    root = decode_keyed_archive(data)
    if not isinstance(root, dict):
        raise ValueError("unexpected LuLu rule list")
    found = []
    for entry in root.values():
        for raw in entry["rules"]:
            authorities = (raw.get("csInfo") or {}).get("signatureAuthorities") or [None]
            found.append(
                LuluRule(
                    path=raw["path"],
                    action=int(raw["action"]),
                    address=str(raw["endpointAddr"]),
                    port=str(raw["endpointPort"]),
                    disabled=bool(raw.get("isDisabled")),
                    authority=authorities[0],
                )
            )
    return found


def team_of(rule: LuluRule) -> str | None:
    """The Apple team ID in a Developer ID signer, such as "... (3MU9H2V9Y9)"."""
    match = re.search(r"\(([A-Z0-9]{10})\)$", rule.authority or "")
    return match[1] if match else None


def coverage_problems(executables: Mapping[str, str], rules: Sequence[LuluRule]) -> list[str]:
    """Each program needs an enabled block-everything rule and no enabled allow rule."""
    found = []
    for name, path in executables.items():
        mine = [r for r in rules if r.path == path and not r.disabled]
        if not any(r.action == BLOCK and r.address == "*" and r.port == "*" for r in mine):
            found.append(f"{name}: no rule blocks all connections from {path}")
        if any(r.action != BLOCK for r in mine):
            found.append(f"{name}: a rule allows connections from {path}")
    return found


def settings_problems(preferences: Mapping[str, object]) -> list[str]:
    return [
        f"LuLu setting {key} is {preferences.get(key)!r} (must be {expected})"
        for key, expected in PINNED_SETTINGS.items()
        if preferences.get(key) is not expected
    ]
