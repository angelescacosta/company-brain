"""T44: the user guide and demo script cover every launcher and every enabled mode by its
exact name, include a supervised restore exercise and the Chrome advice, and stay short."""

import re
import tomllib
from pathlib import Path

from company_brain.openwebui_pipe import MODES

REPO = Path(__file__).resolve().parents[1]
GUIDE = REPO / "docs" / "USER_GUIDE.md"
DEMO = REPO / "docs" / "DEMO_SCRIPT.md"
PROFILE = tomllib.loads((REPO / "config" / "profile.toml").read_text())["demo_profile"]
ENABLED = ["answer", "summarise"] + (["draft"] if PROFILE == "full" else [])


def test_every_launcher_and_mode_by_its_exact_name() -> None:
    """T44.AC1."""
    guide = GUIDE.read_text(encoding="utf-8")
    launchers = sorted(p.stem for p in (REPO / "launchers").glob("*.command"))
    assert launchers
    for name in launchers:
        assert f"**{name}**" in guide, name
    for mode in ENABLED:
        assert f"**{MODES[mode]}**" in guide, mode
    if PROFILE == "full":
        assert "Word" in guide and "PDF" in guide


def test_supervised_restore_exercise() -> None:
    """T44.AC2."""
    guide = GUIDE.read_text(encoding="utf-8")
    section = guide.split("## Practise a restore", 1)[1].split("\n## ", 1)[0]
    assert "supervised" in section.lower()
    assert "**Restore backup**" in section and "recovery key" in section
    assert len(re.findall(r"^\d+\. ", section, re.MULTILINE)) >= 4


def test_chrome_advice() -> None:
    """T44.AC3."""
    guide = GUIDE.read_text(encoding="utf-8").lower()
    assert "chrome" in guide and "faster" in guide
    assert "slower to switch" in guide


def test_two_pages_and_a_ten_minute_demo() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    assert len(guide.split()) <= 1100, len(guide.split())  # about two printed pages
    demo = DEMO.read_text(encoding="utf-8")
    minutes = [int(m) for m in re.findall(r"\((\d+) min\)", demo)]
    assert minutes and sum(minutes) <= 10, minutes
    for mode in ENABLED:
        assert f"**{MODES[mode]}**" in demo, mode
    assert "fictional" in demo.lower()
