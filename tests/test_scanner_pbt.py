"""T15 PBT: the scanner stays inside the allowed folders and opens no file.

Generated trees mix valid client, fictional, public and methodology files with
symlinks (including `..` targets), Unicode names, hidden files and decoy `Tax`
and `Bank` folders holding supported-looking files. Protects tax and bank
documents and unlisted files.
"""

import os
import sys
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from company_brain.scanner import scan

# Lower case only: the Mac's disk ignores case, so "Ab" and "aB" are one folder.
name = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789 -_ñéüçå",
    min_size=1,
    max_size=10,
).filter(lambda s: s.strip() == s and s not in {"public", "shared"})
ext = st.sampled_from([".pdf", ".docx", ".vtt", ".png", ".heic", ".JPG", ".xlsx", ".txt"])
client_sub = st.sampled_from(["Transcripts", "Proposals", "Contracts", "Reports"])
fictional_sub = st.sampled_from(
    ["Transcripts", "Proposals", "Contracts", "Reports", "Receipts", "Receipts/Owner photos"]
)

SUPPORTED = {".pdf", ".docx", ".vtt", ".png", ".heic", ".jpg", ".xlsx"}


@st.composite
def trees(draw: st.DrawFn) -> dict[str, list[tuple[str, str]]]:
    """Files to create, by category; each entry is (relative path, kind)."""
    files: list[tuple[str, str]] = []
    for _ in range(draw(st.integers(0, 6))):
        files.append((f"Clients/{draw(name)}/{draw(client_sub)}/{draw(name)}{draw(ext)}", "f"))
    for _ in range(draw(st.integers(0, 6))):
        path = f"Test data (fictional)/Clients/{draw(name)}/{draw(fictional_sub)}/{draw(name)}"
        files.append((path + draw(ext), "f"))
    for _ in range(draw(st.integers(0, 3))):
        files.append((f"Test data (fictional)/Public/{draw(name)}{draw(ext)}", "f"))
    for _ in range(draw(st.integers(0, 3))):
        files.append((f"Methodology/{draw(name)}{draw(ext)}", "f"))
    decoys = [
        (f"{folder}/{draw(name)}{draw(ext)}", "f")
        for folder in ("Tax", "Bank", "Tax/2025", "Personal")
        for _ in range(draw(st.integers(0, 2)))
    ]
    odd = [
        (f"Clients/{draw(name)}/.{draw(name)}.pdf", "f"),
        (f"Clients/{draw(name)}/loose{draw(ext)}", "f"),
        (f"Clients/{draw(name)}/Other/{draw(name)}.pdf", "f"),
    ]
    links = draw(
        st.lists(
            st.sampled_from(
                [
                    "../../../outside/secret.pdf",
                    "../../../../outside/secret.pdf",
                    "../../../Tax/return.pdf",
                    "../../../../Company Brain Backups/archive.pdf",
                    "/etc/hosts",
                    "../../../Methodology/method.pdf",
                ]
            ),
            max_size=4,
        )
    )
    link_entries = [(f"Clients/{draw(name)}/Proposals/link{i}.pdf", t) for i, t in enumerate(links)]
    return {"files": files + odd, "decoys": decoys, "links": link_entries}


def expected_scope(relative: str) -> str | None:
    parts = relative.split("/")
    if parts[0] == "Clients" and len(parts) == 4:
        return parts[1]
    if parts[:2] == ["Test data (fictional)", "Clients"]:
        return f"fictional:{parts[2]}"
    if parts[:2] == ["Test data (fictional)", "Public"]:
        return "public"
    if parts[0] == "Methodology":
        return "shared"
    return None


@contextmanager
def recorded_opens() -> Iterator[list[str]]:
    opened: list[str] = []
    active = [True]

    def hook(event: str, args: tuple[object, ...]) -> None:
        if active[0] and event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            opened.append(os.fsdecode(args[0]))

    sys.addaudithook(hook)
    try:
        yield opened
    finally:
        active[0] = False


def build(base: Path, tree: dict[str, list[tuple[str, str]]]) -> Path:
    root = base / "root"
    (base / "outside").mkdir()
    (base / "outside" / "secret.pdf").write_text("outside")
    (base / "Company Brain Backups").mkdir()
    (base / "Company Brain Backups" / "archive.pdf").write_text("backup")
    root.mkdir()
    (root / "Tax").mkdir()
    (root / "Tax" / "return.pdf").write_text("tax")
    (root / "Methodology").mkdir()
    (root / "Methodology" / "method.pdf").write_text("method")
    for relative, _ in tree["files"] + tree["decoys"]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content")
    for relative, target in tree["links"]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() and not path.is_symlink():
            path.symlink_to(target)
    return root


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(trees())
def test_scanner_stays_inside_allowed_folders(tree: dict[str, list[tuple[str, str]]]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp).resolve()
        root = build(base, tree)
        backups = base / "Company Brain Backups"
        with recorded_opens() as opened:
            report = scan(root, backup_dir=backups)
        assert [p for p in opened if p.startswith(str(base))] == []

        seen = set()
        for item in report.files:
            resolved = item.path.resolve()
            assert resolved.is_relative_to(root)
            assert not resolved.is_relative_to(backups)
            relative = resolved.relative_to(root).as_posix()
            assert relative.split("/")[0] not in {"Tax", "Bank", "Personal"}
            assert item.scope == unicodedata.normalize("NFC", expected_scope(relative) or "")
            assert item.extension in SUPPORTED
            assert resolved not in seen
            seen.add(resolved)

        expected = {
            (root / r).resolve()
            for r, _ in tree["files"]
            if expected_scope(r) is not None
            and Path(r).suffix.lower() in SUPPORTED
            and not any(part.startswith(".") for part in r.split("/"))
            and "/Other/" not in r
            and not r.split("/")[-1].startswith("loose")
        }
        expected.add((root / "Methodology" / "method.pdf").resolve())
        assert seen == expected
