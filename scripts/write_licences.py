"""Write LICENCES.md from the pinned configuration and the installed packages (T12).

Usage: uv run python scripts/write_licences.py
"""

from pathlib import Path

from company_brain.licences import render

REPO = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    (REPO / "LICENCES.md").write_text(render(REPO), encoding="utf-8")
    print("wrote LICENCES.md")
