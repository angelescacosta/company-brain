"""The licence register, `LICENCES.md` (T12).

Models, tools and documents have hand-checked licences (written here, with
where they were checked); Python packages take theirs from the installed
package metadata, so every package in `uv.lock` has a row. Regenerate with
`uv run python scripts/write_licences.py`; `tests/test_licence_register.py`
fails when the file is out of date.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path

# Model licences as stated on each official model card (checked 2026-10-21).
MODELS: dict[str, tuple[str, str]] = {
    "qwen3.5:9b": ("Apache-2.0", "https://huggingface.co/Qwen/Qwen3.5-9B"),
    "qwen3.5:4b": ("Apache-2.0", "https://huggingface.co/Qwen/Qwen3.5-4B"),
    "gemma4:12b": ("Apache-2.0", "https://huggingface.co/google/gemma-4-12B-it"),
    "qwen3-embedding:0.6b": ("Apache-2.0", "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B"),
    "bge-m3:latest": ("MIT", "https://huggingface.co/BAAI/bge-m3"),
    "embeddinggemma-2:270m": ("Apache-2.0", "https://huggingface.co/google/embeddinggemma-2"),
    "BAAI/bge-reranker-v2-m3": ("Apache-2.0", "https://huggingface.co/BAAI/bge-reranker-v2-m3"),
    "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1": (
        "Apache-2.0",
        "https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    ),
    "vectara/hallucination_evaluation_model": (
        "Apache-2.0",
        "https://huggingface.co/vectara/hallucination_evaluation_model",
    ),
    "google/flan-t5-base": ("Apache-2.0", "https://huggingface.co/google/flan-t5-base"),
    "HuggingFaceTB--SmolVLM-256M-Instruct": (
        "Apache-2.0",
        "https://huggingface.co/HuggingFaceTB/SmolVLM-256M-Instruct",
    ),
    "docling-project--DocumentFigureClassifier-v2.5": (
        "MIT",
        "https://huggingface.co/docling-project/DocumentFigureClassifier-v2.5",
    ),
    "docling-project--docling-layout-heron": (
        "Apache-2.0",
        "https://huggingface.co/docling-project/docling-layout-heron",
    ),
    "docling-project--docling-layout-heron-onnx": (
        "Apache-2.0",
        "https://huggingface.co/docling-project/docling-layout-heron-onnx",
    ),
    "docling-project--docling-models": (
        "CDLA-Permissive-2.0 and Apache-2.0",
        "https://huggingface.co/docling-project/docling-models",
    ),
    "ibm-granite--granite-vision-4.1-4b": (
        "Apache-2.0",
        "https://huggingface.co/ibm-granite/granite-vision-4.1-4b",
    ),
    "o200k_base": ("MIT", "https://github.com/openai/tiktoken"),
    "cl100k_base": ("MIT", "https://github.com/openai/tiktoken"),
}

# Tools installed outside uv.lock (config/versions.toml), from each vendor's licence file.
TOOLS: dict[str, tuple[str, str]] = {
    "ollama": ("MIT (bundled llama.cpp and MLX: MIT)", "https://github.com/ollama/ollama"),
    "postgres_app": ("PostgreSQL License", "https://postgresapp.com"),
    "pgvector": ("PostgreSQL License", "https://github.com/pgvector/pgvector"),
    "lulu": ("GPL-3.0", "https://github.com/objective-see/LuLu"),
    "google_drive": (
        "Proprietary, Google Workspace terms (the owner's existing subscription)",
        "https://www.google.com/drive/download/",
    ),
    "age": ("BSD-3-Clause", "https://github.com/FiloSottile/age"),
    "open_webui": (
        "Open WebUI License (BSD-3-Clause with a branding clause)",
        "https://github.com/open-webui/open-webui",
    ),
    "playwright_chromium": ("BSD-3-Clause (Chromium)", "https://www.chromium.org"),
    "uv": ("MIT OR Apache-2.0", "https://github.com/astral-sh/uv"),
}

LICENCE_NAMES = {
    "CC-BY-4.0": "CC-BY-4.0 (credit the source)",
    "OGL-UK-3.0": "Open Government Licence v3.0 (credit the source)",
}

HEADER = """# Licences

Every model, tool, library, font, dataset and public document this project uses,
with its licence. Written by `scripts/write_licences.py` (T12); do not edit by hand.
Python package licences come from the installed package metadata.

The fictional test documents (T11, T11R, T13) are this project's own work.
Credit for the public documents: CUAD by The Atticus Project (CC BY 4.0); CORD by
NAVER Clova (CC BY 4.0); UK Statistics Authority annual report, Crown copyright,
Open Government Licence v3.0; Ararteko informe anual 2024 (CC BY 4.0).
"""


def _table(title: str, columns: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> str:
    lines = [f"## {title}", "", "| " + " | ".join(columns) + " |"]
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    lines += ["| " + " | ".join(cell.replace("|", "/") for cell in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def package_licence(name: str) -> str:
    try:
        meta = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return "not installed on this Mac (platform-specific); see its PyPI page"
    if meta.get("License-Expression"):
        return str(meta["License-Expression"])
    classifiers = [
        c.split(" :: ")[-1]
        for c in meta.get_all("Classifier") or []
        if c.startswith("License :: ") and c != "License :: OSI Approved"
    ]
    if classifiers:
        return ", ".join(classifiers)
    text = (meta.get("License") or "").strip()
    first = text.splitlines()[0].strip() if text else ""
    if first and len(first) <= 80:
        return first
    return "stated in the package's licence file (see its PyPI page)"


def render(repo: Path) -> str:
    models = tomllib.loads((repo / "config" / "models.toml").read_text())
    versions = tomllib.loads((repo / "config" / "versions.toml").read_text())
    fonts = tomllib.loads((repo / "config" / "fonts.toml").read_text())["font"]
    public = json.loads((repo / "data" / "manifests" / "public.json").read_text())["files"]
    lock = tomllib.loads((repo / "uv.lock").read_text())

    model_names = [
        *models["ollama"],
        *models["huggingface"],
        *models["docling"],
        *models["tiktoken"]["files"],
    ]
    model_rows = [(name, *MODELS[name]) for name in model_names]
    tool_rows = [
        (name, TOOLS[name][0], TOOLS[name][1], str(entry.get("version", entry.get("revision", ""))))
        for name, entry in versions["tools"].items()
    ]
    font_rows = [(f["name"], f["licence"], f["source"]) for f in fonts]
    public_rows = [
        (Path(e["path"]).name, LICENCE_NAMES[e["licence"]], re.sub(r"%20", " ", e["source"]))
        for e in public
    ]
    packages = sorted(
        (p["name"], p.get("version", "")) for p in lock["package"] if p["name"] != "company-brain"
    )
    package_rows = [(name, package_licence(name), version) for name, version in packages]
    parts = [
        HEADER,
        _table("Models", ("Model", "Licence", "Source"), model_rows),
        _table("Tools", ("Tool", "Licence", "Source", "Version"), tool_rows),
        _table("Fonts", ("Font", "Licence", "Source"), font_rows),
        _table("Public documents and datasets", ("File", "Licence", "Source"), public_rows),
        _table("Python packages (uv.lock)", ("Package", "Licence", "Version"), package_rows),
    ]
    return "\n".join(parts)
