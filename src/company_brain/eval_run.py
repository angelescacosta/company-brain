"""Evaluation runs on the tuning questions (T34; the locked run is T38).

For each question: Answer mode runs search, reranking (or not), the answering
model and the output gate; Summarise mode runs on the question's call. The run
records retrieval metrics, required-fact correctness, refusals, summary
coverage, adversarial behaviour and the hard blockers; then the answering
model is unloaded. The local judge (Ragas faithfulness and response relevancy,
Gemma 4 12B) scores the saved answers afterwards in its own process, one at a
time and resumable (`judge_saved`), because judging in the same process as the
answering models was too slow on this Mac. The firewall is checked before and
after. Results append to `reports/eval_runs.csv`; per-question records go to
`reports/runs/<run_id>/questions.jsonl` (git-ignored).
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
import types
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from company_brain import evaluation as ev
from company_brain import guardrails
from company_brain.answer import Answer
from company_brain.convert import Converted
from company_brain.questions import NOT_FOUND
from company_brain.rerank import KEEP, Selection
from company_brain.retrieval import SHARED

REPO = Path(__file__).resolve().parents[2]
RUNS_CSV = REPO / "reports" / "eval_runs.csv"
CSV_FIELDS = [
    "run_id",
    "date",
    "split",
    "stage",
    "config",
    "metric",
    "language",
    "value",
    "count",
    "total",
]
JUDGE_MODEL = "gemma4:12b"
JUDGE_EMBEDDINGS = "bge-m3"


@dataclass
class Deps:
    """Everything a run calls, injected so tests can pass fakes."""

    retrieve: Callable[[str, str], list[Any]]  # (question, scope) -> fused hits
    rerank: Callable[[str, list[Any]], Selection] | None  # None: reranker off
    respond: Callable[[str, Selection], tuple[guardrails.Gated, Answer]]
    summarise: Callable[[Converted], Any]
    convert: Callable[[str], Converted]  # root-relative path -> converted file
    claim_score: Callable[[str, str], float] | None = None
    judge: Callable[[list[dict[str, Any]]], list[dict[str, float]]] | None = None
    firewall: Callable[[], list[str]] = field(default=lambda: [])
    unload: Callable[[], None] = field(default=lambda: None)


@dataclass
class Outcome:
    run_id: str
    rows: list[dict[str, Any]]
    records: list[dict[str, Any]]
    blockers: list[str]
    passed: bool


def _matching_element(converted: Converted, location: dict[str, Any], hint: str) -> str:
    keys = {k: v for k, v in location.items() if k not in {"image"}}
    candidates = [
        e
        for e in converted.elements
        if all(e.provenance.get(k) == v for k, v in keys.items() if k in e.provenance)
        and any(k in e.provenance for k in keys)
    ] or list(converted.elements)
    if hint:
        best = [
            e for e in candidates if ev.covers(e.text, hint) or hint.casefold() in e.text.casefold()
        ]
        if best:
            return best[0].text
    return " ".join(e.text for e in candidates[:3])


def span_texts(
    question: dict[str, Any], facts: dict[str, dict[str, Any]], convert: Callable[[str], Converted]
) -> list[str]:
    spans = []
    for fact_id in question["required_facts"]:
        fact = facts[fact_id]
        location = fact.get("location") or {"file": fact["files"][0], "image": True}
        hint = str(
            fact.get("decision")
            or fact.get("action")
            or fact.get("text")
            or fact.get("label")
            or ""
        )
        spans.append(_matching_element(convert(location["file"]), location, hint))
    return spans


def _shown_unsourced(gated: guardrails.Gated, answer: Answer) -> list[str]:
    sources = {s.number: f"{s.hit.label}\n{s.hit.text}" for s in answer.sources}
    found = []
    for line in gated.facts_text.splitlines():
        cited = guardrails.parse_citations(line)
        if not cited or guardrails.NOT_IN_SOURCES in line and len(line) < 30:
            continue
        check = guardrails.check_fact(line, cited, sources, answer.language)
        found += [v for v in check.failed_values if v not in guardrails.NOT_IN_SOURCES]
    return found


def _leak(found_scope: str, path: str, scope: str, folder: str) -> bool:
    """Another client's data: a wrong scope, or a file outside the client's folder.

    The shared methodology is allowed in every chat (T21.AC2).
    """
    if found_scope == SHARED:
        return not path.startswith("Methodology/")
    return found_scope != scope or not path.startswith(folder)


def run(
    questions: Sequence[dict[str, Any]],
    facts: dict[str, dict[str, Any]],
    deps: Deps,
    config: dict[str, Any],
    split: str = "tuning",
    stage: str = "manual",
    locked_run: bool = False,
) -> Outcome:
    """`locked_run` is passed only by T38's final evaluation, which keeps everything in memory."""
    if split != "tuning" and not (split == "locked" and locked_run):
        raise PermissionError("only the tuning split runs here; the locked set is T38's")
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    firewall_before = deps.firewall()
    records: list[dict[str, Any]] = []
    results: list[ev.QuestionResult] = []
    spans_all: dict[str, list[str]] = {}
    ranked_all: dict[str, list[str]] = {}
    added_all: dict[str, list[str]] = {}
    for q in questions:
        scope = f"fictional:{q['client']}"
        folder = f"Test data (fictional)/Clients/{q['client']}/"
        record: dict[str, Any] = {
            "id": q["id"],
            "client": q["client"],  # ownership, so deleting a client removes its lines (T42)
            "language": q["language"],
            "category": q["category"],
            "question": q["question"],
        }
        started = time.monotonic()
        leaks: list[str] = []
        unsourced: list[str] = []
        if q["mode"] == "summarise":
            file = q["required_sources"][0]["file"]
            summary = deps.summarise(deps.convert(file))
            found = [
                fid
                for fid in q["required_facts"]
                if ev.fact_correct(facts[fid], summary.text, q["language"], mode="summarise")
            ]
            share = len(found) / len(q["required_facts"])
            passed = share >= 0.9
            record.update(answer=summary.text, score=share, sources=summary.sources)
            leaks += [
                s["file"]
                for s in summary.sources
                if not str(s["file"]).startswith((folder, "Methodology/"))
            ]
        else:
            hits = deps.retrieve(q["question"], scope)
            leaks += [h.rel_path for h in hits if _leak(h.scope, h.rel_path, scope, folder)]
            if deps.rerank is not None:
                selection = deps.rerank(q["question"], hits[:40])
            else:
                selection = Selection(
                    kept=tuple((h, h.score) for h in hits[:KEEP]), not_found=not hits
                )
            gated, answer = deps.respond(q["question"], selection)
            unsourced = _shown_unsourced(gated, answer)
            text = gated.text
            if q["category"] == "unanswerable":
                passed = answer.not_found and text == NOT_FOUND[q["language"]]
                share = 1.0 if passed else 0.0
            else:
                ok = [
                    fid
                    for fid in q["required_facts"]
                    if ev.fact_correct(facts[fid], text, q["language"])
                ]
                share = len(ok) / len(q["required_facts"])
                forbidden = [m for m in q.get("must_not_contain", []) if m.lower() in text.lower()]
                passed = share == 1.0 and not forbidden
                record["forbidden_shown"] = forbidden
                spans_all[q["id"]] = span_texts(q, facts, deps.convert)
                ranked_all[q["id"]] = [f"{h.label}\n{h.text}" for h in hits]
                added_all[q["id"]] = [f"{h.label}\n{h.text}" for h, _s in selection.counterparts]
            support = []
            if deps.claim_score is not None:
                for fact in answer.facts:
                    cited = " ".join(
                        f"{s.hit.label}\n{s.hit.text}"
                        for s in answer.sources
                        if s.number in fact.sources
                    )
                    support.append(deps.claim_score(cited, fact.text) >= guardrails.CLAIM_FLOOR)
            record.update(
                answer=text,
                score=share,
                sources=gated.sources,
                warnings=gated.warnings,
                passages=[
                    {"chunk_id": h.chunk_id, "file": h.rel_path, "spans": list(h.spans)}
                    for h in hits[:KEEP]
                ],
                citation_support=(sum(support) / len(support)) if support else None,
            )
            if not answer.not_found:
                record["judge_input"] = {
                    "id": q["id"],
                    "question": q["question"],
                    "answer": text,
                    # The full text of every cited source: an excerpt made correct claims look
                    # unsupported to the judge in the first run (faithfulness 0.29).
                    "contexts": [
                        f"{s.hit.label}\n{s.hit.text}"
                        for s in answer.sources
                        if s.number in {n for f in answer.facts for n in f.sources}
                    ]
                    or [""],
                }
        record.update(
            passed=passed,
            seconds=round(time.monotonic() - started, 1),
            leaks=leaks,
            unsourced_shown=unsourced,
        )
        records.append(record)
        results.append(
            ev.QuestionResult(
                q["id"], q["language"], q["category"], passed, share, True, leaks, unsourced
            )
        )
    deps.unload()
    firewall_after = deps.firewall()
    blockers = ev.blockers(results)
    blockers += [f"firewall before the run: {p}" for p in firewall_before]
    blockers += [f"firewall after the run: {p}" for p in firewall_after]
    rows = _rows(
        run_id, split, stage, config, questions, records, spans_all, ranked_all, added_all, blockers
    )
    return Outcome(run_id, rows, records, blockers, passed=not blockers)


def _rows(
    run_id: str,
    split: str,
    stage: str,
    config: dict[str, Any],
    questions: Sequence[dict[str, Any]],
    records: list[dict[str, Any]],
    spans: dict[str, list[str]],
    ranked: dict[str, list[str]],
    added: dict[str, list[str]],
    blockers: list[str],
) -> list[dict[str, Any]]:
    base = {
        "run_id": run_id,
        "date": f"{datetime.now():%Y-%m-%d %H:%M}",
        "split": split,
        "stage": stage,
        "config": json.dumps(config, sort_keys=True),
    }
    rows = []

    def add(metric: str, language: str, value: float, count: int, total: int) -> None:
        rows.append(
            {
                **base,
                "metric": metric,
                "language": language,
                "value": round(value, 4),
                "count": count,
                "total": total,
            }
        )

    for language in ("en", "es", "all"):
        mine = [r for r in records if language == "all" or r["language"] == language]
        ids = [
            q["id"]
            for q in questions
            if q["id"] in spans and (language == "all" or q["language"] == language)
        ]
        if ids:
            s = [spans[i] for i in ids]
            r = [ranked[i] for i in ids]
            add(
                "recall@5",
                language,
                ev.recall_at_k(s, r, 5),
                round(ev.recall_at_k(s, r, 5) * len(ids)),
                len(ids),
            )
            add(
                "evidence_coverage",
                language,
                ev.evidence_coverage(s, r, [added[i] for i in ids]),
                round(ev.evidence_coverage(s, r, [added[i] for i in ids]) * len(ids)),
                len(ids),
            )
            add("mrr", language, ev.mrr(s, r), len(ids), len(ids))
        for metric, category in (
            ("fact_correct", None),
            ("refusal", "unanswerable"),
            ("summary_coverage", "summary"),
            ("adversarial", "adversarial"),
        ):
            group = [
                x
                for x in mine
                if category is None
                and x["category"] not in ("unanswerable", "summary", "adversarial")
                or x["category"] == category
            ]
            if group:
                good = sum(1 for x in group if x["passed"])
                add(metric, language, good / len(group), good, len(group))
        for metric in ("faithfulness", "answer_relevancy", "citation_support"):
            values = [x[metric] for x in mine if isinstance(x.get(metric), (int, float))]
            if values:
                add(metric, language, sum(values) / len(values), len(values), len(mine))
        passed = sum(1 for x in mine if x["passed"])
        add("questions_passed", language, passed / len(mine) if mine else 0.0, passed, len(mine))
    add("hard_blockers", "all", float(len(blockers)), len(blockers), len(records))
    return rows


def append_rows(
    rows: Sequence[dict[str, Any]], path: Path | None = None, locked_run: bool = False
) -> None:
    """Add rows to `reports/eval_runs.csv`; only tuning rows, except T38's aggregate rows."""
    allowed = {"tuning", "locked"} if locked_run else {"tuning"}
    if any(row.get("split") not in allowed for row in rows):
        raise PermissionError("only tuning rows are written here; the locked set is T38's")
    path = path or RUNS_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if new:
            writer.writeheader()
        writer.writerows(rows)


def write(outcome: Outcome) -> Path:
    append_rows(outcome.rows)
    folder = REPO / "reports" / "runs" / outcome.run_id
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "questions.jsonl").open("w", encoding="utf-8") as handle:
        for record in outcome.records:
            handle.write(
                json.dumps({**record, "split": "tuning"}, ensure_ascii=False, default=str) + "\n"
            )
    from company_brain import dashboard

    dashboard.rebuild_after("evaluation")
    return folder


def judge_saved(
    folder: Path,
    judge: Callable[[list[dict[str, Any]]], list[dict[str, float]]],
    progress: Callable[[str], None] = lambda _m: None,
) -> int:
    """Score a saved run's answers one at a time, saving after each, so it can resume.

    Run in a fresh process after the answers (the answering run's models are gone).
    Appends faithfulness and answer relevancy rows for the run when every answer is scored.
    """
    path = folder / "questions.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    todo = [r for r in records if r.get("judge_input") and "faithfulness" not in r]
    for number, record in enumerate(todo, start=1):
        try:
            scores: dict[str, Any] = dict(judge([record["judge_input"]])[0])
        except Exception as error:  # noqa: BLE001 - a failed judgement is recorded, the run goes on
            scores = {
                "faithfulness": float("nan"),
                "answer_relevancy": float("nan"),
                "judge_error": type(error).__name__,
            }
        record.update({k: v for k, v in scores.items() if k != "id"})
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in records),
            encoding="utf-8",
        )
        progress(f"judged {number} of {len(todo)}")
    if any(r.get("judge_input") and "faithfulness" not in r for r in records):
        return len(todo)
    run_id = folder.name
    base = {
        "run_id": run_id,
        "date": f"{datetime.now():%Y-%m-%d %H:%M}",
        "split": "tuning",
        "stage": "judge",
        "config": json.dumps({"judge": JUDGE_MODEL}),
    }
    rows = []
    for language in ("en", "es", "all"):
        mine = [r for r in records if language == "all" or r["language"] == language]
        for metric in ("faithfulness", "answer_relevancy"):
            values = [
                r[metric]
                for r in mine
                if isinstance(r.get(metric), (int, float)) and r[metric] == r[metric]
            ]
            if values:
                rows.append(
                    {
                        **base,
                        "metric": metric,
                        "language": language,
                        "value": round(sum(values) / len(values), 4),
                        "count": len(values),
                        "total": len(mine),
                    }
                )
    with RUNS_CSV.open("a", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=CSV_FIELDS).writerows(rows)
    from company_brain import dashboard

    dashboard.rebuild_after("judging")
    return len(todo)


def ragas_judge(ollama_url: str) -> Callable[[list[dict[str, Any]]], list[dict[str, float]]]:
    """Ragas faithfulness and response relevancy, judged by a local model through Ollama.

    Ragas 0.4.3 imports a Vertex AI class that langchain-community 0.4.2 no longer has,
    and langchain-community no longer has an Ollama chat model; a stand-in module and the
    OpenAI-compatible endpoint of the local Ollama cover both (D35).
    """
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path.home() / ".cache" / "tiktoken"))
    name = "langchain_community.chat_models.vertexai"
    if name not in sys.modules:
        stand_in = types.ModuleType(name)
        stand_in.ChatVertexAI = type("ChatVertexAI", (), {})  # type: ignore[attr-defined]
        sys.modules[name] = stand_in
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, ResponseRelevancy
    from ragas.run_config import RunConfig

    base = ollama_url.rstrip("/") + "/v1"
    llm = LangchainLLMWrapper(
        ChatOpenAI(
            model=JUDGE_MODEL,
            base_url=base,
            api_key="local",  # type: ignore[arg-type]
            temperature=0,
            reasoning_effort="none",  # thinking off: 5x fewer tokens, no timeouts (D35)
            timeout=600,
        )
    )
    embeddings = LangchainEmbeddingsWrapper(
        OpenAIEmbeddings(
            model=JUDGE_EMBEDDINGS,
            base_url=base,
            api_key="local",  # type: ignore[arg-type]
            check_embedding_ctx_length=False,
        )
    )

    def judge(items: list[dict[str, Any]]) -> list[dict[str, float]]:
        if not items:
            return []
        dataset = EvaluationDataset.from_list(
            [
                {
                    "user_input": i["question"],
                    "response": i["answer"],
                    "retrieved_contexts": i["contexts"],
                }
                for i in items
            ]
        )
        result = evaluate(
            dataset,
            metrics=[Faithfulness(), ResponseRelevancy()],
            llm=llm,
            embeddings=embeddings,
            show_progress=False,
            run_config=RunConfig(timeout=300, max_workers=1, max_retries=1),
        )
        if not hasattr(result, "to_pandas"):
            raise RuntimeError("the judge did not return results")
        table = result.to_pandas()
        out = []
        for item, (_, row) in zip(items, table.iterrows(), strict=True):
            out.append(
                {
                    "id": item["id"],
                    "faithfulness": float(row.get("faithfulness", float("nan"))),
                    "answer_relevancy": float(row.get("answer_relevancy", float("nan"))),
                }
            )
        return out

    return judge


def report_lines(outcome: Outcome) -> list[str]:
    """Plain summary with counts next to every percentage (T34.AC4)."""
    lines = [f"Run {outcome.run_id}: {'passed' if outcome.passed else 'BLOCKED'}"]
    for row in outcome.rows:
        if row["metric"] in ("mrr", "faithfulness", "answer_relevancy", "citation_support"):
            note = ", small sample" if row["total"] < ev.SMALL_SAMPLE else ""
            lines.append(
                f"{row['metric']} ({row['language']}): {row['value']:.2f} "
                f"(n = {row['count']} of {row['total']}{note})"
            )
        elif row["metric"] != "hard_blockers":
            lines.append(
                f"{row['metric']} ({row['language']}): {ev.percent(row['count'], row['total'])}"
            )
    lines += [f"BLOCKER: {b}" for b in outcome.blockers]
    return lines


def relative(path: str) -> str:
    return PurePosixPath(path).as_posix()
