"""Locked final evaluation report (T38).

The locked questions are decrypted in memory once and never written to disk. The report
holds only aggregates: every metric per language with counts and targets, the hashes of
the frozen configuration and of the locked file, the hard-blocker count and the status.
"""

import csv
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "reports" / "final_eval.md"
LOCKED = REPO / "eval" / "locked.json.age"

# metric -> (target, label) from the PRD's T38 description
TARGETS = {
    "recall@5": (0.85, "finds the right document in the top 5"),
    "mrr": (0.70, "rank of the right document (1 = always first)"),
    "faithfulness": (0.90, "everything said is in the sources"),
    "answer_relevancy": (0.80, "sticks to what was asked"),
    "citation_validity": (1.00, "no figure shown without a source"),
    "refusal": (0.90, "says “I don’t know” when there is no answer"),
}
INFORMATIONAL = ("questions_passed", "fact_correct", "summary_coverage", "adversarial")
LANGUAGES = ("en", "es", "all")


def judge_rows(
    scored: Sequence[tuple[str, dict[str, float]]], base: dict[str, Any], totals: dict[str, int]
) -> list[dict[str, Any]]:
    """Faithfulness and relevancy per language from (language, scores) pairs."""
    rows = []
    for language in LANGUAGES:
        mine = [s for lang, s in scored if language == "all" or lang == language]
        for metric in ("faithfulness", "answer_relevancy"):
            values = [
                s[metric]
                for s in mine
                if isinstance(s.get(metric), (int, float)) and s[metric] == s[metric]
            ]
            if values:
                rows.append(
                    {
                        **base,
                        "metric": metric,
                        "language": language,
                        "value": round(sum(values) / len(values), 4),
                        "count": len(values),
                        "total": totals[language],
                    }
                )
    return rows


def citation_rows(
    unsourced_by_language: dict[str, int], totals: dict[str, int], base: dict[str, Any]
) -> list[dict[str, Any]]:
    """Share of answers that show no value without a source."""
    rows = []
    for language in LANGUAGES:
        bad = (
            sum(unsourced_by_language.values())
            if language == "all"
            else unsourced_by_language.get(language, 0)
        )
        total = totals[language]
        rows.append(
            {
                **base,
                "metric": "citation_validity",
                "language": language,
                "value": round((total - bad) / total, 4) if total else 0.0,
                "count": total - bad,
                "total": total,
            }
        )
    return rows


def status(rows: Sequence[dict[str, Any]], hard_blockers: int) -> tuple[str, list[str]]:
    """'blocked' with any hard blocker; 'pilot, below target' if any target is missed."""
    missed = [
        f"{r['metric']} ({r['language']}): {float(r['value']):.2f} < {TARGETS[r['metric']][0]:.2f}"
        for r in rows
        if r["metric"] in TARGETS and float(r["value"]) < TARGETS[r["metric"]][0]
    ]
    if hard_blockers:
        return "blocked", missed
    return ("pilot, below target" if missed else "meets every target"), missed


def report(
    rows: Sequence[dict[str, Any]],
    hard_blockers: int,
    config_sha: str,
    locked_sha: str,
    run_id: str,
    date: str,
) -> str:
    verdict, missed = status(rows, hard_blockers)
    by = {(r["metric"], r["language"]): r for r in rows}
    lines = [
        "# Final evaluation on the locked questions (T38)",
        "",
        f"Run `{run_id}`, {date}. One run only: the locked set is never re-run after changes.",
        "",
        f"* Status: **{verdict}**",
        f"* Hard blockers: {hard_blockers}",
        f"* Frozen configuration `config/selected.toml` SHA-256: `{config_sha}`",
        f"* Locked file `eval/locked.json.age` SHA-256: `{locked_sha}`",
        "",
        "| metric | language | value | count | target |",
        "|---|---|---|---|---|",
    ]
    for metric in (*TARGETS, *INFORMATIONAL):
        for language in LANGUAGES:
            row = by.get((metric, language))
            if row is None:
                continue
            target = f"≥ {TARGETS[metric][0]:.2f}" if metric in TARGETS else "–"
            lines.append(
                f"| {metric} | {language} | {float(row['value']):.3f} | "
                f"{row['count']}/{row['total']} | {target} |"
            )
    if missed:
        lines += ["", "Below target:", *[f"* {m}" for m in missed]]
    return "\n".join(lines) + "\n"


def locked_runs(runs_csv: Path) -> set[str]:
    if not runs_csv.exists():
        return set()
    with runs_csv.open(encoding="utf-8") as handle:
        return {r["run_id"] for r in csv.DictReader(handle) if r["split"] == "locked"}


def parse(text: str) -> dict[str, Any]:
    """The report's table and header facts, for the T38 checks."""
    table = []
    facts: dict[str, Any] = {}
    for line in text.splitlines():
        if line.startswith("* Hard blockers:"):
            facts["hard_blockers"] = int(line.split(":")[1])
        elif line.startswith("* Status:"):
            facts["status"] = line.split("**")[1]
        elif "SHA-256: `" in line:
            name = "config_sha" if "selected.toml" in line else "locked_sha"
            facts[name] = line.split("`")[-2]
        elif line.startswith("| ") and not line.startswith("| metric") and "---" not in line:
            metric, language, value, count, _target = [
                c.strip() for c in line.strip("|").split("|")
            ]
            table.append((metric, language, float(value), count))
    facts["table"] = table
    return facts
