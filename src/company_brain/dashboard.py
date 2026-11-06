"""Results dashboard (T34D): one static page built from the saved result files only.

The build opens only the files named in `config/dashboard_inputs.toml`, the
manifest-listed documents and images (through T9's guarded reader, so their
bytes match the manifest) and its own converted copies (checked against their
conversion record). It never opens the sealed questions or their key. The page
has no script, no link and no URL other than embedded `data:` images, carries a
restrictive content policy, and is written atomically. Every chart has its
numbers in a table below it.

Display rules: rates as percentages with one decimal and their denominator
("88.6%, n = 44"); scores (MRR, faithfulness, response relevancy, ratings) with
two decimals and their denominator; seconds with one decimal; "pending" when
there is no data, and a measured zero shown as a value. Green means the target
is met by the unrounded value.

Blind-rating protection: until `reports/blind_ratings_unblinded.csv` exists with
its SHA-256 recorded next to it, no answer text, per-model answer metric or
rating appears anywhere on the page.
"""

from __future__ import annotations

import base64
import contextlib
import csv
import fnmatch
import hashlib
import html
import io
import json
import os
import re
import statistics
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
POLICY = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
BAR = 300.0
TARGETS = {
    "recall@5": 0.85,
    "mrr": 0.7,
    "faithfulness": 0.9,
    "answer_relevancy": 0.8,
    "citation_support": 1.0,
    "refusal": 0.9,
}
SCORES = frozenset({"mrr", "faithfulness", "answer_relevancy"})
SEARCH_METRICS = ("recall@5", "mrr")
ANSWER_METRICS = ("fact_correct", "faithfulness", "answer_relevancy", "citation_support", "refusal")
LANGUAGES = ("en", "es", "all")
PAGE = "dashboard.html"
STALE_PAGE = "dashboard_out_of_date.html"
RECORD = "dashboard_build.json"
PASSAGE_KEYS = frozenset({"chunk_id", "file", "spans"})


def _audit(path: str) -> None:
    """Called with every path the build opens (tests replace it with a recorder)."""


class BuildFailed(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    root: Path  # the project folder (reports/, config/, data/manifests/)
    docs_root: Path  # the documents folder the manifests are relative to
    copies: Path  # the dashboard's converted copies

    @property
    def reports(self) -> Path:
        return self.root / "reports"


def real_paths() -> Paths:
    from company_brain.drive import load_sources
    from company_brain.settings import Settings

    root = load_sources(REPO / "config" / "sources.toml")["root"]
    return Paths(REPO, root, Settings.from_env().data_dir / "dashboard-copies")


# Reading -----------------------------------------------------------------------------


def _spec(paths: Paths) -> dict[str, dict[str, Any]]:
    path = paths.root / "config" / "dashboard_inputs.toml"
    _audit(str(path))
    return tomllib.loads(path.read_text(encoding="utf-8"))


def allowed(paths: Paths, path: Path) -> bool:
    """Whether the build may open `path` (an input, a converted copy or a listed document)."""
    resolved = Path(os.path.realpath(path))
    root = Path(os.path.realpath(paths.root))
    if resolved == root / "config" / "dashboard_inputs.toml":
        return True
    for base in (paths.copies, paths.docs_root):
        if resolved.is_relative_to(os.path.realpath(base)):
            return True
    if not resolved.is_relative_to(root):
        return False
    relative = resolved.relative_to(root).as_posix()
    spec = tomllib.loads((REPO / "config" / "dashboard_inputs.toml").read_text(encoding="utf-8"))
    return any(fnmatch.fnmatchcase(relative, entry["path"]) for entry in spec.values())


def _read(paths: Paths, path: Path) -> bytes:
    if not allowed(paths, path):
        raise BuildFailed(f"{path.name} is not a dashboard input")
    _audit(str(path))
    return path.read_bytes()


def input_files(paths: Paths, spec: dict[str, dict[str, Any]] | None = None) -> list[Path]:
    spec = spec or _spec(paths)
    found: set[Path] = set()
    for entry in spec.values():
        found |= {p for p in paths.root.glob(entry["path"]) if p.is_file()}
    return sorted(found)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unblinded(paths: Paths) -> bool:
    ratings = paths.reports / "blind_ratings_unblinded.csv"
    recorded = paths.reports / "blind_ratings_unblinded.csv.sha256"
    if not (ratings.exists() and recorded.exists()):
        return False
    return _read(paths, recorded).decode().strip() == _sha(_read(paths, ratings))


def fingerprint(paths: Paths) -> dict[str, Any]:
    inputs = {
        p.relative_to(paths.root).as_posix(): _sha(_read(paths, p)) for p in input_files(paths)
    }
    return {"inputs": inputs, "unblinded": unblinded(paths)}


def _check_fields(name: str, found: Iterable[str], entry: dict[str, Any]) -> None:
    found = set(found)
    required, optional = set(entry.get("fields", [])), set(entry.get("optional", []))
    if not required <= found:
        raise BuildFailed(f"{name} lacks fields: {', '.join(sorted(required - found))}")
    if not found <= required | optional:
        raise BuildFailed(
            f"{name} has unknown fields: {', '.join(sorted(found - required - optional))}"
        )


def _csv(paths: Paths, spec: dict[str, Any], key: str) -> list[dict[str, str]] | None:
    entry = spec[key]
    path = paths.root / entry["path"]
    if not path.exists():
        return None
    reader = csv.DictReader(io.StringIO(_read(paths, path).decode("utf-8")))
    _check_fields(entry["path"], reader.fieldnames or [], entry)
    return list(reader)


def _eval_rows(paths: Paths, spec: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in _csv(paths, spec, "eval_runs") or []:
        if row["split"] == "locked":
            continue  # never used
        if row["split"] != "tuning":
            raise BuildFailed(f"eval_runs.csv has an unknown split value {row['split']!r}")
        try:
            config = json.loads(row["config"])
            value = float(row["value"])
            total = int(row["total"])
        except ValueError as error:
            raise BuildFailed(f"eval_runs.csv has an unreadable row ({error})") from None
        rows.append({**row, "config": config, "value": value, "total": total})
    return rows


def _question_runs(paths: Paths, spec: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    entry = spec["questions"]
    runs: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(paths.root.glob(entry["path"])):
        records = []
        for line in _read(paths, path).decode("utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            _check_fields("questions.jsonl", record, entry)
            if record["split"] != "tuning":
                raise BuildFailed(f"questions.jsonl has a {record['split']!r} split record")
            for passage in record.get("passages", []):
                if set(passage) - PASSAGE_KEYS:
                    raise BuildFailed("questions.jsonl has unknown passage fields")
            records.append(record)
        runs[path.parent.name] = records
    return runs


def _final_table(paths: Paths, spec: dict[str, Any]) -> list[dict[str, str]] | None:
    """Only the aggregate table (header: metric, language, value, count, target)."""
    path = paths.root / spec["final_eval"]["path"]
    if not path.exists():
        return None
    lines = _read(paths, path).decode("utf-8").splitlines()
    header = spec["final_eval"]["fields"]
    for number, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and cells == header:
            rows = []
            for row in lines[number + 2 :]:
                if not row.strip().startswith("|"):
                    break
                values = [c.strip() for c in row.strip().strip("|").split("|")]
                rows.append(dict(zip(header, values, strict=True)))
            return rows
    raise BuildFailed("final_eval.md has no aggregate table")


# Formatting ---------------------------------------------------------------------------


def config_label(config: dict[str, Any]) -> str:
    embedding, budget, reranker = (config.get(k) for k in ("embedding", "budget", "reranker"))
    return f"{embedding}, budget {budget}, reranker {reranker}"


def figure(metric: str, value: float, n: int) -> str:
    if metric in SCORES or metric == "rating":
        return f"{value:.2f}, n = {n}"
    return f"{value * 100:.1f}%, n = {n}"


def seconds(value: float) -> str:
    return f"{value:.1f} s"


_URL = re.compile(r"(?i)\b(https?|ftp|file|data|javascript|mailto)(:)")


def text(value: object) -> str:
    """Escaped, with URL schemes broken so no displayed text is an address."""
    return html.escape(_URL.sub(r"\1[:]", str(value)), quote=True)


def cell(key: str, content: str, status: str = "") -> str:
    return f'<td data-key="{text(key)}" class="{status}">{text(content)}</td>'


def pending(key: str, note: str = "") -> str:
    extra = f" <em>{text(note)}</em>" if note else ""
    return f'<p><span data-key="{key}">pending</span>{extra}</p>'


def status_of(metric: str, value: float, target: float | None = None) -> str:
    goal = TARGETS.get(metric) if target is None else target
    if goal is None:
        return ""
    return "green" if value >= goal else "red"


def latest(rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    ordered = sorted(rows, key=lambda r: (r["date"], r["run_id"]))
    return ordered[-1] if ordered else None


# Sections -----------------------------------------------------------------------------


def _chart(bars: list[tuple[str, str, float]], target: float | None) -> str:
    """Horizontal bars (label, key, value 0..1) with an optional target line."""
    height = 18 * len(bars) + 10
    parts = [f'<svg width="560" height="{height}" role="img" aria-label="chart">']
    for number, (label, key, value) in enumerate(bars):
        y = 5 + 18 * number
        parts.append(f'<text x="0" y="{y + 11}" font-size="11">{text(label)}</text>')
        parts.append(
            f'<rect data-key="{text(key)}" x="250" y="{y}" width="{value * BAR:.1f}" '
            f'height="13" fill="#4a6fa5"/>'
        )
    if target is not None:
        x = 250 + target * BAR
        parts.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}" stroke="#c0392b"/>')
    parts.append("</svg>")
    return "".join(parts)


def section_search(rows: list[dict[str, Any]]) -> str:
    search = [r for r in rows if r["metric"] in SEARCH_METRICS and "embedding" in r["config"]]
    if not search:
        return pending("search")
    labels = sorted({config_label(r["config"]) for r in search})
    out = []
    for metric in SEARCH_METRICS:
        bars, table = [], []
        for label in labels:
            cells = []
            for language in LANGUAGES:
                key = f"search|{label}|{metric}|{language}"
                row = latest(
                    r
                    for r in search
                    if config_label(r["config"]) == label
                    and r["metric"] == metric
                    and r["language"] == language
                )
                if row is None:
                    cells.append(cell(key, "pending"))
                    continue
                cells.append(
                    cell(
                        key,
                        figure(metric, row["value"], row["total"]),
                        status_of(metric, row["value"]),
                    )
                )
                bars.append((f"{label} ({language})", key, max(0.0, min(1.0, row["value"]))))
            table.append(f"<tr><th>{text(label)}</th>{''.join(cells)}</tr>")
        head = "".join(f"<th>{lang}</th>" for lang in LANGUAGES)
        target = TARGETS.get(metric)
        out.append(
            f"<h3>{text(metric)} (target {target})</h3>{_chart(bars, target)}"
            f"<table><tr><th>Setting</th>{head}</tr>{''.join(table)}</table>"
        )
    return "".join(out)


def _models(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Run ID -> answer model, for every run whose configuration names one."""
    return {r["run_id"]: r["config"]["answer_model"] for r in rows if "answer_model" in r["config"]}


_SEVERITY = {"": -1, "pass": 0, "inconclusive": 1, "fail": 2}


def _worst_runs(benchmarks: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """One row per model; T33 writes a row per run: keep the highest memory, worst pressure."""
    worst: dict[str, dict[str, str]] = {}
    for row in benchmarks:
        known = worst.setdefault(row["model"], dict(row))
        if int(row["peak_memory_bytes"]) > int(known["peak_memory_bytes"]):
            known["peak_memory_bytes"] = row["peak_memory_bytes"]
        verdict = row.get("pressure_verdict") or ""
        if _SEVERITY.get(verdict, 1) > _SEVERITY.get(known.get("pressure_verdict") or "", 1):
            known["pressure_verdict"] = verdict
    return worst


def section_answers(
    rows: list[dict[str, Any]],
    runs: dict[str, list[dict[str, Any]]],
    benchmarks: list[dict[str, str]],
    show_answers: bool,
) -> str:
    models_of = _models(rows)
    peak = _worst_runs(benchmarks)
    names = sorted(set(models_of.values()) | set(peak))
    if not names:
        return pending("answers")
    speed_rows = []
    for model in names:
        timed = [run for run in sorted(runs) if models_of.get(run) == model and runs[run]]
        values = (
            [
                float(r["seconds"])
                for r in runs[timed[-1]]
                if isinstance(r.get("seconds"), (int, float))
            ]
            if timed
            else []
        )
        speed = seconds(statistics.median(values)) if values else "pending"
        bench = peak.get(model)
        memory = f"{int(bench['peak_memory_bytes']) / 1e9:.1f} GB" if bench else "pending"
        pressure = (bench or {}).get("pressure_verdict") or "pending"
        speed_rows.append(
            f"<tr><th>{text(model)}</th>{cell(f'speed|{model}', speed)}"
            f"{cell(f'memory|{model}', memory)}{cell(f'pressure|{model}', pressure)}</tr>"
        )
    out = [
        "<h3>Speed and memory</h3><table><tr><th>Model</th><th>Seconds per answer (median)"
        "</th><th>Peak memory</th><th>Memory pressure</th></tr>" + "".join(speed_rows) + "</table>"
    ]
    if not show_answers:
        out.append(
            pending("answers", "Answer quality is shown after the blind ratings are unblinded.")
        )
        return "".join(out)
    table = []
    for model in names:
        for language in LANGUAGES:
            cells = []
            for metric in ANSWER_METRICS:
                key = f"answers|{model}|{metric}|{language}"
                row = latest(
                    r
                    for r in rows
                    if models_of.get(r["run_id"]) == model
                    and r["metric"] == metric
                    and r["language"] == language
                )
                if row is None:
                    cells.append(cell(key, "pending"))
                else:
                    cells.append(
                        cell(
                            key,
                            figure(metric, row["value"], row["total"]),
                            status_of(metric, row["value"]),
                        )
                    )
            table.append(f"<tr><th>{text(model)} ({language})</th>{''.join(cells)}</tr>")
    head = "".join(f"<th>{m}</th>" for m in ANSWER_METRICS)
    out.append(
        f"<h3>Answer quality</h3><table><tr><th>Model</th>{head}</tr>{''.join(table)}</table>"
    )
    return "".join(out)


def section_ratings(ratings: list[dict[str, str]] | None, show_answers: bool) -> str:
    if not show_answers or not ratings:
        return pending("ratings")
    rows = []
    for model in sorted({r["model"] for r in ratings}):
        scores = [float(r["score"]) for r in ratings if r["model"] == model]
        rows.append(
            f"<tr><th>{text(model)}</th>"
            + cell(f"rating|{model}", figure("rating", statistics.fmean(scores), len(scores)))
            + "</tr>"
        )
    return f"<table><tr><th>Model</th><th>Mean rating</th></tr>{''.join(rows)}</table>"


class Library:
    """Listed documents and images, read through the guarded reader; converted copies cached."""

    def __init__(self, paths: Paths) -> None:
        from company_brain.content_guard import GuardedReader, Manifest

        self.paths = paths
        self.manifest = Manifest.load(paths.root / "data" / "manifests")

        def read_once(path: str) -> bytes:
            _audit(path)
            with open(path, "rb") as handle:
                return handle.read()

        self.reader = GuardedReader(paths.docs_root, self.manifest, read_bytes=read_once)
        self._docling: Any = None
        self._elements: dict[str, list[dict[str, Any]] | None] = {}
        self.notes: list[str] = []
        self._records_path = paths.copies / "records.json"
        self._records: dict[str, str] = (
            json.loads(_read(paths, self._records_path)) if self._records_path.exists() else {}
        )

    def _verified(self, relative: str) -> Any:
        from company_brain.content_guard import DigestMismatch, OutsideRoot, Unlisted

        try:
            return self.reader.read(self.paths.docs_root / relative)
        except (Unlisted, DigestMismatch, OutsideRoot, OSError):
            return None

    def elements(self, relative: str) -> list[dict[str, Any]] | None:
        if relative in self._elements:
            return self._elements[relative]
        found = self._load(relative)
        self._elements[relative] = found
        if found is None:
            self.notes.append(
                f"{Path(relative).name}: dropped (not listed, or changed since it was listed)"
            )
        return found

    def _load(self, relative: str) -> list[dict[str, Any]] | None:
        expected = self.manifest.digests.get(relative)
        if expected is None:
            return None
        copy = self.paths.copies / f"{expected}.json"
        if copy.exists():
            data = _read(self.paths, copy)
            if self._records.get(expected) == _sha(data):
                return list(json.loads(data)["elements"])
        verified = self._verified(relative)
        if verified is None:
            return None
        from company_brain import convert

        if relative.lower().endswith(".pdf") and self._docling is None:
            self._docling = convert.Docling()
        converted = convert.convert(verified, self._docling)
        if converted.status != "converted":
            return None
        elements = [
            {"kind": e.kind, "text": e.text, "provenance": e.provenance} for e in converted.elements
        ]
        data = json.dumps(
            {"relative": relative, "elements": elements},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode()
        self.paths.copies.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(data)
        self._records[expected] = _sha(data)
        self._records_path.write_text(json.dumps(self._records, sort_keys=True))
        return elements

    def passage(self, relative: str, spans: list[dict[str, Any]]) -> str | None:
        elements = self.elements(relative)
        if elements is None:
            return None
        parts = []
        for span in spans:
            for element in elements:
                provenance = element["provenance"]
                matches = all(provenance.get(k) == v for k, v in span.items())
                if matches and element["text"] and element["text"] not in parts:
                    parts.append(element["text"])
        return " ".join(parts)

    def thumbnail(self, relative: str) -> str | None:
        verified = self._verified(relative)
        if verified is None:
            return None
        from PIL import Image

        data = verified.data
        if relative.lower().endswith((".heic", ".heif")):
            from company_brain.convert import heic_to_jpeg

            data = heic_to_jpeg(data)
        with Image.open(io.BytesIO(data)) as image:
            small = image.convert("RGB")
            small.thumbnail((160, 160))
            out = io.BytesIO()
            small.save(out, format="PNG", optimize=False)
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def section_ocr(ocr: list[dict[str, str]] | None, library: Library) -> str:
    if not ocr:
        return pending("ocr")
    rows = []
    for group, field in sorted({(r["group"], r["field"]) for r in ocr}):
        mine = [r for r in ocr if r["group"] == group and r["field"] == field]
        exact = sum(r["result"] == "exact" for r in mine)
        rows.append(
            f"<tr><th>{text(group)}</th><th>{text(field)}</th>"
            f"{cell(f'ocr|{group}|{field}', figure('accuracy', exact / len(mine), len(mine)))}</tr>"
        )
    misses = []
    for row in sorted(
        (r for r in ocr if r["result"] != "exact"),
        key=lambda r: (r["group"], r["file"], r["field"]),
    ):
        picture = library.thumbnail(row["file"])
        image = (
            f'<img alt="thumbnail" src="{picture}">'
            if picture
            else "<p>Thumbnail dropped: the image is not listed or changed since it was listed.</p>"
        )
        misses.append(
            f"<details><summary>{text(Path(row['file']).name)}: {text(row['field'])} "
            f"expected {text(row['expected'])}, read {text(row['recognised'])}</summary>"
            f"{image}<pre>{text(row['raw_text'])}</pre></details>"
        )
    return (
        "<table><tr><th>Group</th><th>Field</th><th>Read exactly</th></tr>"
        + "".join(rows)
        + "</table><h3>Misses</h3>"
        + ("".join(misses) or "<p>None.</p>")
    )


def section_questions(
    runs: dict[str, list[dict[str, Any]]], library: Library, show_answers: bool
) -> str:
    filled = [run for run in sorted(runs) if runs[run]]
    if not filled:
        return pending("questions")
    out = [f"<p>Run {text(filled[-1])}: tuning questions only.</p>"]
    for record in sorted(runs[filled[-1]], key=lambda r: r["id"]):
        passages = []
        for passage in record.get("passages", []):  # summaries read a whole document
            found = library.passage(passage["file"], passage["spans"])
            body = (
                text(found)
                if found is not None
                else "Passage dropped: the document is not listed or changed since it was listed."
            )
            passages.append(f"<li><b>{text(Path(passage['file']).name)}</b>: {body}</li>")
        answer = ""
        if show_answers:
            verdict = "passed" if record.get("passed") else "failed"
            answer = f"<p><b>Answer ({verdict}):</b> {text(record.get('answer', ''))}</p>"
        out.append(
            f"<details><summary>{text(record['id'])} ({text(record['language'])}, "
            f"{text(record['category'])}): {text(record['question'])}</summary>"
            f"<ul>{''.join(passages)}</ul>{answer}</details>"
        )
    return "".join(out)


def section_scorecard(final: list[dict[str, str]] | None) -> str:
    if not final:
        return pending("scorecard")
    rows = []
    for row in final:
        try:
            value, count, target = float(row["value"]), int(row["count"]), float(row["target"])
        except ValueError:
            raise BuildFailed("final_eval.md has an unreadable aggregate row") from None
        key = f"final|{row['metric']}|{row['language']}"
        rows.append(
            f"<tr><th>{text(row['metric'])}</th><th>{text(row['language'])}</th>"
            + cell(
                key, figure(row["metric"], value, count), status_of(row["metric"], value, target)
            )
            + f"<td>{text(target)}</td></tr>"
        )
    return (
        "<table><tr><th>Metric</th><th>Language</th><th>Result</th><th>Target</th></tr>"
        + "".join(rows)
        + "</table>"
    )


STYLE = (
    "body{font-family:-apple-system,Helvetica,sans-serif;margin:16px;max-width:980px;"
    "background:#fff;color:#222}table{border-collapse:collapse;margin:8px 0 18px}"
    "td,th{border:1px solid #ccc;padding:3px 8px;text-align:left;font-size:13px}"
    ".green{background:#d8f0d8}.red{background:#f6d6d6}pre{white-space:pre-wrap;font-size:12px}"
    "details{margin:4px 0}summary{cursor:pointer}"
)


def render(paths: Paths) -> str:
    spec = _spec(paths)
    show = unblinded(paths)
    rows = _eval_rows(paths, spec)
    runs = _question_runs(paths, spec)
    benchmarks = _csv(paths, spec, "benchmark_full") or _csv(paths, spec, "benchmark_early") or []
    ocr = _csv(paths, spec, "ocr")
    ratings = _csv(paths, spec, "blind_ratings") if show else None
    final = _final_table(paths, spec)
    library = Library(paths)
    sections = [
        ("1. Search quality", section_search(rows)),
        ("2. Answer models", section_answers(rows, runs, benchmarks, show)),
        ("3. Blind ratings", section_ratings(ratings, show)),
        ("4. Image reading", section_ocr(ocr, library)),
        ("5. Question browser", section_questions(runs, library, show)),
        ("6. Final scorecard", section_scorecard(final)),
    ]
    notes = "".join(f"<li>{text(n)}</li>" for n in sorted(set(library.notes)))
    body = "".join(f"<h2>{title}</h2>{content}" for title, content in sections)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Test results</title><style>{STYLE}</style></head><body>"
        "<h1>Company Brain: test results</h1>"
        "<p>Fictional and public test documents only. Locked test questions are never shown.</p>"
        f"{body}" + (f"<h2>Notes</h2><ul>{notes}</ul>" if notes else "") + "</body></html>\n"
    )


def _write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(data, encoding="utf-8")
    os.replace(temporary, path)


def build(paths: Paths) -> dict[str, Any]:
    """Build the page and the build record; a failure is recorded, never raised."""
    try:
        state = fingerprint(paths)
        page = render(paths)
        record: dict[str, Any] = {**state, "result": "succeeded", "reason": ""}
        _write(paths.reports / PAGE, page)
    except (BuildFailed, ValueError, KeyError, TypeError) as error:
        reason = (
            str(error) if isinstance(error, BuildFailed) else f"{type(error).__name__}: {error}"
        )
        try:
            state = fingerprint(paths)
        except BuildFailed:
            state = {"inputs": {}, "unblinded": False}
        record = {**state, "result": "failed", "reason": reason}
    record["built"] = datetime.now().isoformat(timespec="seconds")
    _write(paths.reports / RECORD, json.dumps(record, indent=1, sort_keys=True) + "\n")
    return record


def rebuild_after(trigger: str, paths: Paths | None = None) -> None:
    """Called at the end of every evaluation, judging, benchmark and OCR run, after the
    unblinding (T36) and after `reports/final_eval.md` is written (T38). Never raises."""
    from company_brain import isolation

    if paths is None and os.environ.get(isolation.ENV_TEST_FLAG):
        return  # tests never rebuild the real page
    with contextlib.suppress(Exception):  # the launcher rebuilds or flags the page
        build(paths or real_paths())


def show(paths: Paths, opener: Callable[[Path], None]) -> Path:
    """ "Show test results": open the page only when its record matches the current inputs."""
    record_path = paths.reports / RECORD
    record = json.loads(record_path.read_text()) if record_path.exists() else {}
    page = paths.reports / PAGE
    try:
        current = fingerprint(paths)
    except BuildFailed as error:
        current = {"inputs": None, "unblinded": None, "error": str(error)}
    fresh = (
        record.get("result") == "succeeded"
        and record.get("inputs") == current["inputs"]
        and record.get("unblinded") == current["unblinded"]
        and page.exists()
    )
    if not fresh:
        record = build(paths)
    if record.get("result") == "succeeded":
        opener(page)
        return page
    stale = paths.reports / STALE_PAGE
    _write(
        stale,
        (
            '<!doctype html><html><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">'
            "<title>Test results out of date</title></head><body>"
            "<h1>The test results are out of date</h1>"
            f"<p>The last build, at {text(record.get('built', 'unknown time'))}, failed: "
            f"{text(record.get('reason', 'unknown reason'))}.</p>"
            "<p>The older page is not shown because it may not match the current results.</p>"
            "</body></html>\n"
        ),
    )
    opener(stale)
    return stale
