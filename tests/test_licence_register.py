"""T12: LICENCES.md has a licence row for every model, tool, library, font and public file."""

import json
import tomllib
from pathlib import Path

from company_brain import licences

REPO = Path(__file__).resolve().parents[1]


def rows() -> dict[str, str]:
    """First column -> licence column, for every table row in LICENCES.md."""
    found = {}
    for line in (REPO / "LICENCES.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 2 and set(cells[0]) - set("- "):
            found[cells[0]] = cells[1]
    return found


def test_every_listed_item_has_a_licence() -> None:
    """T12.AC2."""
    register = rows()
    models = tomllib.loads((REPO / "config" / "models.toml").read_text())
    versions = tomllib.loads((REPO / "config" / "versions.toml").read_text())
    fonts = tomllib.loads((REPO / "config" / "fonts.toml").read_text())["font"]
    public = json.loads((REPO / "data" / "manifests" / "public.json").read_text())["files"]
    required = [
        *models["ollama"],
        *models["huggingface"],
        *models["docling"],
        *models["tiktoken"]["files"],
        *versions["tools"],
        *versions["libraries"],
        *(f["name"] for f in fonts),
        *(Path(e["path"]).name for e in public),
    ]
    missing = [item for item in required if not register.get(item)]
    assert not missing, missing
    assert all(licence and licence.lower() != "unknown" for licence in register.values())


def test_every_locked_python_package_is_listed() -> None:
    lock = tomllib.loads((REPO / "uv.lock").read_text())
    names = {p["name"] for p in lock["package"] if p["name"] != "company-brain"}
    assert names <= set(rows())


def test_register_is_up_to_date() -> None:
    assert (REPO / "LICENCES.md").read_text(encoding="utf-8") == licences.render(REPO)
