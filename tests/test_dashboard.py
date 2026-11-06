"""T34D: the results dashboard, built from fixture result files in a temporary tree.

Expected figures are computed here from the fixture values under the display rules
(percentages with one decimal and their denominator, scores with two decimals,
seconds with one decimal, "pending" for no data, 0 shown as a value).
"""

import csv
import hashlib
import io
import json
import re
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PIL import Image

from company_brain import dashboard

REPO = Path(__file__).resolve().parents[1]
CONFIG_A = {
    "answer_model": "qwen3.5:4b",
    "budget": 500,
    "embedding": "bge-m3:latest",
    "reranker": "on",
}
CONFIG_B = {**CONFIG_A, "budget": 300}
HOSTILE = '<script>alert(1)</script> <a href="http://evil.example/x">go</a> <img src=x>'
ANSWER = "ANSWER-TEXT-9f3a the fee is EUR 36,500."
RECEIPT = "Test data (fictional)/Clients/Acme/Receipts/Receipt 1 taxi.png"
CALL = "Test data (fictional)/Clients/Acme/Transcripts/Call 2026-08-27.vtt"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def eval_rows(
    run: str, date: str, config: dict, values: dict, split: str = "tuning", stage: str = "baseline"
) -> list[dict[str, object]]:
    """values: {(metric, language): (value, count, total)}."""
    return [
        {
            "run_id": run,
            "date": date,
            "split": split,
            "stage": stage,
            "config": json.dumps(config, sort_keys=True),
            "metric": metric,
            "language": language,
            "value": value,
            "count": count,
            "total": total,
        }
        for (metric, language), (value, count, total) in values.items()
    ]


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (400, 300), (200, 30, 30)).save(out, format="PNG")
    return out.getvalue()


def vtt() -> bytes:
    return (
        "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n<v Owner>The fee is EUR 36,500.\n\n"
        f"00:00:06.000 --> 00:00:09.000\n<v Client>{HOSTILE}\n"
    ).encode()


def record(qid: str, language: str, files: list[str], **extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": qid,
        "client": "Acme",
        "language": language,
        "category": "fact",
        "question": f"Question {qid}?",
        "answer": ANSWER,
        "score": 1.0,
        "passed": True,
        "seconds": 12.25,
        "sources": [],
        "warnings": [],
        "leaks": [],
        "forbidden_shown": [],
        "unsourced_shown": [],
        "split": "tuning",
        "citation_support": 1.0,
        "passages": [{"chunk_id": "i:c:0", "file": f, "spans": [{"cue": 1}]} for f in files],
    }
    return {**base, **extra}


@pytest.fixture
def tree(tmp_path: Path) -> dashboard.Paths:
    root, docs = tmp_path / "repo", tmp_path / "docs"
    (root / "config").mkdir(parents=True)
    shutil.copyfile(
        REPO / "config" / "dashboard_inputs.toml", root / "config" / "dashboard_inputs.toml"
    )
    for rel, data in ((RECEIPT, png()), (CALL, vtt())):
        (docs / rel).parent.mkdir(parents=True, exist_ok=True)
        (docs / rel).write_bytes(data)
    manifests = root / "data" / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "fictional.json").write_text(
        json.dumps(
            {
                "files": [
                    {"path": RECEIPT, "sha256": sha(png())},
                    {"path": CALL, "sha256": sha(vtt())},
                ]
            }
        )
    )
    (manifests / "public.json").write_text(json.dumps({"files": []}))
    return dashboard.Paths(root=root, docs_root=docs, copies=tmp_path / "copies")


def full_inputs(paths: dashboard.Paths) -> None:
    reports = paths.root / "reports"
    write_csv(
        reports / "eval_runs.csv",
        [
            *eval_rows(
                "r1",
                "2026-10-24 15:37",
                CONFIG_A,
                {
                    ("recall@5", "en"): (14 / 15, 14, 15),
                    ("recall@5", "es"): (0.0, 0, 4),
                    ("mrr", "en"): (0.8, 15, 15),
                    ("faithfulness", "all"): (0.88, 30, 30),
                    ("fact_correct", "all"): (25 / 26, 25, 26),
                    ("refusal", "all"): (1.0, 7, 7),
                },
            ),
            *eval_rows("r2", "2026-10-25 09:00", CONFIG_B, {("recall@5", "en"): (1.0, 15, 15)}),
        ],
    )
    run = reports / "runs" / "r1"
    run.mkdir(parents=True)
    (run / "questions.jsonl").write_text(
        json.dumps(record("q-en-1", "en", [CALL]))
        + "\n"
        + json.dumps(record("q-es-1", "es", [CALL], seconds=8.0))
        + "\n"
        + json.dumps(
            {
                k: v
                for k, v in record("q-en-9", "en", [], category="summary").items()
                if k != "passages"
            }
        )
        + "\n"
    )
    write_csv(
        reports / "ocr_receipts.csv",
        [
            {
                "group": "clean",
                "file": RECEIPT,
                "field": "vendor",
                "expected": "Larkspur Cabs",
                "recognised": "Larkspur Cabs",
                "raw_text": "Larkspur Cabs",
                "result": "exact",
            },
            {
                "group": "photo",
                "file": RECEIPT,
                "field": "total",
                "expected": "EUR 12.50",
                "recognised": "EUR 12.80",
                "raw_text": "TOTAL " + HOSTILE,
                "result": "miss",
            },
        ],
    )
    shutil.copyfile(REPO / "reports" / "benchmark_early.csv", reports / "benchmark_early.csv")


def build(paths: dashboard.Paths) -> str:
    record = dashboard.build(paths)
    assert record["result"] == "succeeded", record
    return (paths.root / "reports" / "dashboard.html").read_text(encoding="utf-8")


class Cells(HTMLParser):
    """Every element with a data-key: its text, and bar widths."""

    def __init__(self) -> None:
        super().__init__()
        self.cells: dict[str, str] = {}
        self.bars: dict[str, float] = {}
        self.classes: dict[str, str] = {}
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []
        self._key: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, attrs))
        found = dict(attrs)
        key = found.get("data-key")
        if key and tag == "rect":
            self.bars[key] = float(found["width"] or 0)
        elif key:
            self._key = key
            self.cells[key] = ""
            self.classes[key] = found.get("class") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "span", "li"):
            self._key = None

    def handle_data(self, data: str) -> None:
        if self._key:
            self.cells[self._key] += data


def parse(page: str) -> Cells:
    cells = Cells()
    cells.feed(page)
    return cells


def label(config: dict) -> str:
    return dashboard.config_label(config)


def unblind(paths: dashboard.Paths) -> None:
    path = paths.root / "reports" / "blind_ratings_unblinded.csv"
    write_csv(
        path,
        [
            {"id": "q-en-1", "model": "qwen3.5:4b", "score": 4},
            {"id": "q-es-1", "model": "qwen3.5:4b", "score": 0},
        ],
    )
    Path(f"{path}.sha256").write_text(sha(path.read_bytes()))


def test_figures_match_independent_values(tree: dashboard.Paths) -> None:
    """T34D.AC1 and AC3: tables, bar lengths, green or red, real zeros and pending."""
    full_inputs(tree)
    page = build(tree)
    cells = parse(page)
    a, b = label(CONFIG_A), label(CONFIG_B)
    assert cells.cells[f"search|{a}|recall@5|en"] == "93.3%, n = 15"
    assert cells.cells[f"search|{a}|recall@5|es"] == "0.0%, n = 4"  # a measured zero
    assert cells.cells[f"search|{a}|mrr|en"] == "0.80, n = 15"
    assert cells.cells[f"search|{a}|mrr|es"] == "pending"  # partial run
    assert cells.cells[f"search|{b}|recall@5|en"] == "100.0%, n = 15"
    assert cells.classes[f"search|{a}|recall@5|en"] == "green"  # 0.933 >= 0.85
    assert cells.classes[f"search|{a}|recall@5|es"] == "red"
    assert cells.bars[f"search|{a}|recall@5|en"] == round(14 / 15 * dashboard.BAR, 1)
    assert cells.bars[f"search|{a}|recall@5|es"] == 0.0
    assert cells.cells["speed|qwen3.5:4b"] == "12.2 s"  # median of 12.25, 8.0 and 12.25
    assert cells.cells["memory|qwen3.5:4b"] == "15.3 GB"
    assert cells.cells["ocr|clean|vendor"] == "100.0%, n = 1"
    assert cells.cells["ocr|photo|total"] == "0.0%, n = 1"
    assert cells.cells["ratings"] == "pending"
    assert cells.cells["scorecard"] == "pending"
    assert "Question q-en-9?" in page  # a summary question, which has no passages


def test_empty_inputs_show_pending(tree: dashboard.Paths) -> None:
    """T34D.AC3."""
    cells = parse(build(tree))
    for section in ("search", "answers", "ratings", "ocr", "questions", "scorecard"):
        assert cells.cells[section] == "pending", section


def test_no_network_no_script_no_links(tree: dashboard.Paths) -> None:
    """T34D.AC2: by parsing, and by rendering with every request recorded."""
    full_inputs(tree)
    page = build(tree)
    cells = parse(page)
    assert all(tag != "script" and tag != "a" for tag, _ in cells.tags)
    for _tag, attrs in cells.tags:
        for name, value in attrs:
            assert not name.startswith("on"), name
            if name in ("href", "src", "action", "xlink:href", "srcset", "formaction"):
                assert (value or "").startswith("data:image/png;base64,"), (name, value)
    assert dashboard.POLICY in page
    assert not re.search(r"url\(|@import|https?://", page.replace(dashboard.POLICY, ""))
    from playwright.sync_api import sync_playwright

    requests: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        view = browser.new_page()
        view.on("request", lambda r: requests.append(r.url))
        view.goto((tree.root / "reports" / "dashboard.html").as_uri())
        view.wait_for_load_state("networkidle")
        browser.close()
    assert [r for r in requests if not r.startswith("data:")] == [
        (tree.root / "reports" / "dashboard.html").as_uri()
    ]


def test_nothing_from_answers_before_unblinding(tree: dashboard.Paths) -> None:
    """T34D.AC4."""
    full_inputs(tree)
    page = build(tree)
    cells = parse(page)
    assert "ANSWER-TEXT-9f3a" not in page
    assert not [k for k in cells.cells if k.startswith(("answers|", "rating|", "answer|"))]
    assert "88.0" not in page and "0.88" not in page  # the faithfulness value
    assert "96.2" not in page  # fact correctness 25/26
    unblind(tree)
    page = build(tree)
    cells = parse(page)
    assert "ANSWER-TEXT-9f3a" in page
    assert cells.cells["answers|qwen3.5:4b|faithfulness|all"] == "0.88, n = 30"
    assert cells.cells["answers|qwen3.5:4b|fact_correct|all"] == "96.2%, n = 26"
    assert cells.cells["rating|qwen3.5:4b"] == "2.00, n = 2"  # mean of 4 and a real 0


def test_unblinding_needs_the_recorded_hash(tree: dashboard.Paths) -> None:
    full_inputs(tree)
    unblind(tree)
    (tree.root / "reports" / "blind_ratings_unblinded.csv.sha256").write_text("0" * 64)
    assert "ANSWER-TEXT-9f3a" not in build(tree)


def test_never_opens_the_sealed_file_and_rejects_contamination(
    tree: dashboard.Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T34D.AC5 and AC8."""
    full_inputs(tree)
    reports = tree.root / "reports"
    rows = list(csv.DictReader((reports / "eval_runs.csv").open()))
    locked = eval_rows(
        "LOCKEDRUN",
        "2026-10-30 10:00",
        {**CONFIG_A, "embedding": "locked-only-model"},
        {("recall@5", "en"): (0.4321, 4, 10)},
        split="locked",
    )
    write_csv(reports / "eval_runs.csv", [*rows, *locked])
    (tree.root / "eval").mkdir()
    (tree.root / "eval" / "locked.json.age").write_bytes(b"age-encryption.org/v1 sealed")
    opened: list[str] = []
    monkeypatch.setattr(dashboard, "_audit", opened.append)
    page = build(tree)
    names = [Path(p).name for p in opened]
    assert not [n for n in names if n == "locked.json.age" or n.startswith("seal")], names
    assert all(dashboard.allowed(tree, Path(p)) for p in opened), opened
    assert "LOCKEDRUN" not in page and "locked-only-model" not in page and "43.2" not in page
    (reports / "final_eval.md").write_text(
        "# Final evaluation\n\nSome text the dashboard ignores: q-locked-7.\n\n"
        "| metric | language | value | count | target |\n|---|---|---|---|---|\n"
        "| recall@5 | en | 0.9 | 20 | 0.85 |\n| faithfulness | es | 0.85 | 20 | 0.9 |\n\n"
        "| id | answer |\n|---|---|\n| q-locked-7 | secret answer |\n"
    )
    cells = parse(build(tree))
    assert cells.cells["final|recall@5|en"] == "90.0%, n = 20"
    assert cells.classes["final|recall@5|en"] == "green"
    assert cells.cells["final|faithfulness|es"] == "0.85, n = 20"
    assert cells.classes["final|faithfulness|es"] == "red"
    page = (reports / "dashboard.html").read_text()
    assert "q-locked-7" not in page and "secret answer" not in page
    for bad in ("other", "locked"):
        line = json.dumps(record("q-x", "en", [CALL], split=bad))
        (reports / "runs" / "r1" / "questions.jsonl").write_text(line + "\n")
        assert dashboard.build(tree)["result"] == "failed"
    (reports / "runs" / "r1" / "questions.jsonl").write_text(
        json.dumps(record("q-x", "en", [CALL], extra_field=1)) + "\n"
    )
    assert dashboard.build(tree)["result"] == "failed"
    full_inputs_again = list(csv.DictReader((reports / "eval_runs.csv").open()))
    write_csv(reports / "eval_runs.csv", [{**full_inputs_again[0], "split": "holdout"}])
    record_ = dashboard.build(tree)
    assert record_["result"] == "failed" and "split" in record_["reason"]


def test_hostile_text_and_substitutions_are_inert(tree: dashboard.Paths) -> None:
    """T34D.AC6."""
    full_inputs(tree)
    page = build(tree)
    assert "<script" not in page and "http://evil.example" not in page
    assert "http[:]//evil.example/x" in page  # the address is shown as text, not usable
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page  # shown, escaped, inert
    assert "<a " not in page and "<img src=x" not in page
    assert "The fee is EUR 36,500." in page  # the passage, cut from the converted copy
    # A converted copy edited after it was made: its hash no longer matches the record.
    for copy in tree.copies.glob("*.json"):
        if copy.name != "records.json":
            copy.write_text(copy.read_text().replace("The fee is", "FORGED fee is"))
    page = build(tree)
    assert "FORGED" not in page and "The fee is EUR 36,500." in page
    # Image bytes changed after listing: the thumbnail is dropped with a note.
    (tree.docs_root / RECEIPT).write_bytes(png() + b"tampered")
    page = build(tree)
    assert "data:image/png" not in page and "dropped" in page
    # A source file changed after listing: its passage is dropped too.
    (tree.docs_root / CALL).write_bytes(vtt().replace(b"36,500", b"99,999"))
    shutil.rmtree(tree.copies)
    page = build(tree)
    assert "99,999" not in page and "36,500" not in page.split('data-key="questions"')[-1]


def test_build_record_and_the_launcher(tree: dashboard.Paths) -> None:
    """T34D.AC7: the record lists input hashes and the unblinding state; the launcher opens
    the page only when the record matches, rebuilds otherwise, and shows the out-of-date
    page when the build fails."""
    full_inputs(tree)
    build(tree)
    record_ = json.loads((tree.root / "reports" / "dashboard_build.json").read_text())
    assert record_["result"] == "succeeded" and record_["unblinded"] is False
    assert record_["inputs"]["reports/eval_runs.csv"] == sha(
        (tree.root / "reports" / "eval_runs.csv").read_bytes()
    )
    opened: list[Path] = []
    builds: list[int] = []
    real_build = dashboard.build

    def counting(paths: dashboard.Paths) -> dict:
        builds.append(1)
        return real_build(paths)

    dashboard_build = dashboard.build
    try:
        dashboard.build = counting  # type: ignore[assignment]
        dashboard.show(tree, opened.append)
        assert builds == [] and opened[-1].name == "dashboard.html"
        with (tree.root / "reports" / "eval_runs.csv").open("a") as handle:
            handle.write("")  # unchanged bytes: still no rebuild
        dashboard.show(tree, opened.append)
        assert builds == []
        unblind(tree)  # unblinding state changes
        dashboard.show(tree, opened.append)
        assert builds == [1] and opened[-1].name == "dashboard.html"
        (tree.root / "reports" / "runs" / "r1" / "questions.jsonl").write_text(
            json.dumps(record("q-x", "en", [CALL], split="other")) + "\n"
        )
        dashboard.show(tree, opened.append)
        assert opened[-1].name == "dashboard_out_of_date.html"
        stale = opened[-1].read_text()
        assert "out of date" in stale and "split" in stale
    finally:
        dashboard.build = dashboard_build  # type: ignore[assignment]


def test_every_producer_triggers_a_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T34D.AC7: evaluation, judging (tuning uses the same runs), benchmark and OCR runs."""
    from company_brain import benchmark, eval_run, receipts

    triggers: list[str] = []
    monkeypatch.setattr(dashboard, "rebuild_after", triggers.append)
    monkeypatch.setattr(eval_run, "RUNS_CSV", tmp_path / "eval_runs.csv")
    monkeypatch.setattr(eval_run, "REPO", tmp_path)
    outcome = eval_run.Outcome("r9", rows=[], records=[], blockers=[], passed=True)
    eval_run.write(outcome)
    folder = tmp_path / "reports" / "runs" / "r9"
    (folder / "questions.jsonl").write_text("")
    eval_run.judge_saved(folder, lambda _batch: [])
    benchmark.write_csv(tmp_path / "benchmark.csv", [])
    receipts.write_ocr_report(tmp_path / "ocr.csv", [{"group": "clean"}])
    assert triggers == ["evaluation", "judging", "benchmark", "ocr"]


def test_rebuild_after_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_paths: dashboard.Paths) -> dict:
        raise RuntimeError("disk full")

    monkeypatch.setattr(dashboard, "build", broken)
    dashboard.rebuild_after(
        "evaluation", dashboard.Paths(Path("/nonexistent"), Path("/x"), Path("/y"))
    )
    assert "company_brain.dashboard" in sys.modules


def test_full_benchmark_shows_the_worst_run(tree: dashboard.Paths) -> None:
    """T33 writes one row per run: the page shows the highest memory and the worst pressure."""
    from company_brain.benchmark import FULL_FIELDS

    full_inputs(tree)
    base = {field: "" for field in FULL_FIELDS}
    rows = [
        {**base, "model": "qwen3.5:4b", "peak_memory_bytes": peak, "pressure_verdict": verdict}
        for peak, verdict in [
            (14_000_000_000, "pass"),
            (15_600_000_000, "fail"),
            (13_000_000_000, "pass"),
        ]
    ]
    for row in rows:
        row.update(baseline_memory_bytes=1, swap_growth_bytes=0, first_token_s=1, total_s=1)
        row.update(flagged="no", mode="answer", run=1, cold="no")
    write_csv(tree.root / "reports" / "benchmark_full.csv", rows, FULL_FIELDS)
    cells = parse(build(tree))
    assert cells.cells["memory|qwen3.5:4b"] == "15.6 GB"
    assert cells.cells["pressure|qwen3.5:4b"] == "fail"
