"""T34: the evaluation runner with fake components (no models)."""

import json
from pathlib import Path

import pytest

from company_brain import eval_run, guardrails
from company_brain.answer import Answer, Fact, Source
from company_brain.convert import Converted, Element
from company_brain.questions import NOT_FOUND
from company_brain.rerank import Selection
from company_brain.retrieval import Hit

REPO = Path(__file__).resolve().parents[1]
FOLDER = "Test data (fictional)/Clients/Pellmore Dental Group/"
FACTS = {
    "pellmore-dental-group.contract.fee": {
        "id": "pellmore-dental-group.contract.fee",
        "type": "value",
        "client": "Pellmore Dental Group",
        "attribute": "fee",
        "value": 36500,
        "unit": "EUR",
        "text": "EUR 36,500",
        "location": {"file": FOLDER + "Contracts/Agreement.docx", "paragraph": 5},
    },
}
QUESTIONS = [
    {
        "id": "q-en-01",
        "language": "en",
        "client": "Pellmore Dental Group",
        "category": "fact",
        "mode": "answer",
        "question": "What fee did we agree?",
        "expected_answer": "EUR 36,500.",
        "required_facts": ["pellmore-dental-group.contract.fee"],
        "required_sources": [
            {"file": FOLDER + "Contracts/Agreement.docx", "location": {"paragraph": 5}}
        ],
    },
    {
        "id": "q-en-02",
        "language": "en",
        "client": "Pellmore Dental Group",
        "category": "unanswerable",
        "mode": "answer",
        "question": "How many dentists work there?",
        "expected_answer": NOT_FOUND["en"],
        "required_facts": [],
        "required_sources": [],
    },
]
DOC = Converted(
    FOLDER + "Contracts/Agreement.docx",
    "h",
    "converted",
    elements=(
        Element(
            "paragraph", "3. Fees. The client pays a fixed fee of EUR 36,500.", {"paragraph": 5}
        ),
    ),
)


def hit(path: str = FOLDER + "Contracts/Agreement.docx") -> Hit:
    return Hit(
        "c1",
        "d1",
        "fictional:Pellmore Dental Group",
        "Pellmore Dental Group",
        path,
        "3. Fees. The client pays a fixed fee of EUR 36,500.",
        "Pellmore | Agreement.docx",
        None,
        ({"paragraph": 5},),
        1.0,
    )


def respond(question: str, selection: Selection) -> tuple[guardrails.Gated, Answer]:
    if "dentists" in question:
        answer = Answer(question, "en", NOT_FOUND["en"], not_found=True)
    else:
        answer = Answer(
            question,
            "en",
            "",
            facts=[Fact("The fee is EUR 36,500.", (1,))],
            sources=[Source(1, selection.kept[0][0], 1.0)],
        )
    return guardrails.gate(answer), answer


def deps(leak: bool = False) -> eval_run.Deps:
    return eval_run.Deps(
        retrieve=lambda q, _s: (
            [] if "dentists" in q else [hit(), *([hit("Clients/Other/x.docx")] if leak else [])]
        ),
        rerank=lambda q, hits: Selection(
            kept=tuple((h, 1.0) for h in hits[:5]), not_found=not hits
        ),
        respond=respond,
        summarise=lambda _c: None,
        convert=lambda _p: DOC,
        judge=lambda items: [
            {"id": i["id"], "faithfulness": 1.0, "answer_relevancy": 0.9} for i in items
        ],
    )


def test_a_clean_run_passes_with_counts() -> None:
    outcome = eval_run.run(QUESTIONS, FACTS, deps(), {"model": "test"})
    assert outcome.passed and outcome.blockers == []
    metrics = {(r["metric"], r["language"]): r for r in outcome.rows}
    assert metrics[("recall@5", "en")]["value"] == 1.0 and metrics[("recall@5", "en")]["total"] == 1
    assert metrics[("refusal", "en")]["count"] == 1
    lines = eval_run.report_lines(outcome)
    assert any("(1 of 1, small sample)" in line for line in lines)


def test_a_planted_leak_blocks_the_run() -> None:
    """T34.AC2."""
    outcome = eval_run.run(QUESTIONS, FACTS, deps(leak=True), {"model": "test"})
    assert not outcome.passed and any("client leak" in b for b in outcome.blockers)


def test_a_firewall_problem_blocks_the_run() -> None:
    d = deps()
    d.firewall = lambda: ["LuLu is not running"]
    assert not eval_run.run(QUESTIONS, FACTS, d, {}).passed


def test_the_locked_split_is_refused() -> None:
    with pytest.raises(PermissionError):
        eval_run.run(QUESTIONS, FACTS, deps(), {}, split="locked")


def test_records_are_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eval_run, "RUNS_CSV", tmp_path / "eval_runs.csv")
    monkeypatch.setattr(eval_run, "REPO", tmp_path)
    outcome = eval_run.run(QUESTIONS, FACTS, deps(), {})
    folder = eval_run.write(outcome)
    lines = (folder / "questions.jsonl").read_text().splitlines()
    assert [json.loads(line)["split"] for line in lines] == ["tuning", "tuning"]
    assert (tmp_path / "eval_runs.csv").read_text().startswith("run_id,date,split")


def test_the_judge_scores_a_saved_run_and_can_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(eval_run, "RUNS_CSV", tmp_path / "eval_runs.csv")
    monkeypatch.setattr(eval_run, "REPO", tmp_path)
    folder = eval_run.write(eval_run.run(QUESTIONS, FACTS, deps(), {}))
    calls = []

    def flaky(items):  # type: ignore[no-untyped-def]
        calls.append(items[0]["id"])
        if len(calls) == 1:
            raise TimeoutError("judge too slow")
        return [{"id": items[0]["id"], "faithfulness": 1.0, "answer_relevancy": 0.8}]

    assert eval_run.judge_saved(folder, flaky) == 1  # one answer to judge; the failure is kept
    record = json.loads((folder / "questions.jsonl").read_text().splitlines()[0])
    assert record["judge_error"] == "TimeoutError"
    assert eval_run.judge_saved(folder, flaky) == 0  # nothing left: failures are not retried


def test_the_shared_methodology_is_not_a_leak() -> None:
    """T21.AC2: methodology chunks are eligible in every scope (first run flagged them)."""
    shared = Hit(
        "c9",
        "d9",
        "shared",
        "shared",
        "Methodology/Methodology (EN).docx",
        "Value-based fees.",
        "Methodology",
        None,
        (),
        1.0,
    )
    d = deps()
    d.retrieve = lambda q, _s: [] if "dentists" in q else [hit(), shared]
    outcome = eval_run.run(QUESTIONS, FACTS, d, {})
    assert outcome.passed, outcome.blockers


def test_the_judge_gets_the_full_cited_source() -> None:
    outcome = eval_run.run(QUESTIONS, FACTS, deps(), {})
    contexts = outcome.records[0]["judge_input"]["contexts"]
    assert contexts == [
        "Pellmore | Agreement.docx\n3. Fees. The client pays a fixed fee of EUR 36,500."
    ]
