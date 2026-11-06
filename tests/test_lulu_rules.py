"""T4.AC4 unit tests: reading LuLu's rule list and settings, and the coverage rule.

LuLu stores its rules as an NSKeyedArchiver property list. These tests build
small synthetic archives in the same shape, so no real rule list is needed.
"""

import plistlib
from typing import Any

from company_brain.firewall import (
    ALLOW,
    BLOCK,
    LuluRule,
    coverage_problems,
    decode_keyed_archive,
    lulu_rules,
    settings_problems,
    team_of,
)


class Obj(dict[str, Any]):
    """An archived object whose fields are stored directly (like LuLu's Rule)."""


def archive(root: object) -> bytes:
    objects: list[object] = ["$null"]

    def add(value: object) -> plistlib.UID:
        if value is None:
            return plistlib.UID(0)
        index = len(objects)
        objects.append(None)
        if isinstance(value, Obj):
            stored: object = {k: add(v) for k, v in value.items()}
        elif isinstance(value, dict):
            stored = {
                "NS.keys": [add(k) for k in value],
                "NS.objects": [add(v) for v in value.values()],
            }
        elif isinstance(value, list):
            stored = {"NS.objects": [add(v) for v in value]}
        else:
            stored = value
        if isinstance(stored, dict):
            stored["$class"] = plistlib.UID(0)
        objects[index] = stored
        return plistlib.UID(index)

    top = add(root)
    return plistlib.dumps(
        {
            "$archiver": "NSKeyedArchiver",
            "$version": 100000,
            "$objects": objects,
            "$top": {"root": top},
        },
        fmt=plistlib.FMT_BINARY,
    )


def rule(
    path: str,
    action: int,
    address: str = "*",
    disabled: object = None,
    authority: str | None = None,
) -> Obj:
    cs = Obj(signatureAuthorities=[authority]) if authority else Obj(signatureIdentifier="-")
    return Obj(
        path=path,
        action=action,
        endpointAddr=address,
        endpointPort="*",
        isDisabled=disabled,
        csInfo=cs,
        name=path.rsplit("/", 1)[-1],
    )


def rules_file(*rules: Obj) -> bytes:
    return archive({r["path"]: {"paths": [r["path"]], "rules": [r]} for r in rules})


PY = "/u/.local/share/uv/python/cpython-3.12/bin/python3.12"
OLLAMA = "/u/.local/opt/ollama/ollama"


def test_decode_resolves_nested_objects_and_null() -> None:
    data = archive({"a": [1, None, Obj(x="y")]})
    assert decode_keyed_archive(data) == {"a": [1, None, {"x": "y"}]}


def test_rules_are_read_with_action_and_signer() -> None:
    authority = "Developer ID Application: Infra Technologies, Inc (3MU9H2V9Y9)"
    found = lulu_rules(rules_file(rule(OLLAMA, BLOCK, authority=authority)))
    assert found == [
        LuluRule(
            path=OLLAMA, action=BLOCK, address="*", port="*", disabled=False, authority=authority
        )
    ]
    assert team_of(found[0]) == "3MU9H2V9Y9"


def test_blocked_paths_are_covered() -> None:
    rules = lulu_rules(rules_file(rule(PY, BLOCK), rule(OLLAMA, BLOCK)))
    assert coverage_problems({"python": PY, "ollama": OLLAMA}, rules) == []


def test_missing_allowing_partial_or_disabled_rules_are_problems() -> None:
    rules = lulu_rules(rules_file(rule(PY, ALLOW)))
    assert coverage_problems({"python": PY}, rules)
    rules = lulu_rules(rules_file(rule(PY, BLOCK, address="1.2.3.4")))
    assert coverage_problems({"python": PY}, rules)
    rules = lulu_rules(rules_file(rule(PY, BLOCK, disabled=True)))
    assert coverage_problems({"python": PY}, rules)
    assert coverage_problems({"ollama": OLLAMA}, lulu_rules(rules_file(rule(PY, BLOCK))))


def test_an_allow_rule_next_to_a_block_rule_is_a_problem() -> None:
    data = archive({"k": {"paths": [PY], "rules": [rule(PY, BLOCK), rule(PY, ALLOW)]}})
    assert coverage_problems({"python": PY}, lulu_rules(data))


def test_pinned_settings() -> None:
    good = {
        "allowApple": False,
        "allowInstalled": False,
        "passiveMode": False,
        "disabled": False,
        "blockMode": False,
        "allowDNS": True,
    }
    assert settings_problems(good) == []
    for key in ("allowApple", "allowInstalled", "passiveMode", "disabled"):
        assert settings_problems({**good, key: True}), key
    assert settings_problems({k: v for k, v in good.items() if k != "allowInstalled"})
