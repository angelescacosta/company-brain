"""Locked final evaluation (T38): run the 22 locked questions once with the frozen configuration.

Usage: uv run python scripts/final_eval.py

Close Chrome first. The locked questions are decrypted in a child process and kept in
memory; the child answers them and hands back only what the judge needs, then exits so
its models leave memory before Gemma 4 12B judges. Nothing per question is written to
disk: only aggregate rows (split "locked") and `reports/final_eval.md`.
"""

import importlib.util
import multiprocessing as mp
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]


def answer_locked(queue: Any) -> None:
    import json

    from company_brain import eval_run, freeze, seal
    from company_brain.db import connect

    spec = importlib.util.spec_from_file_location("run_eval", REPO / "scripts" / "run_eval.py")
    run_eval = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(run_eval)  # type: ignore[union-attr]

    selected = freeze.load_selected()
    conn = connect()
    active = conn.execute(
        "SELECT model, chunk_budget FROM brain.index_meta WHERE active"
    ).fetchone()
    conn.close()
    if selected is None or tuple(active) != freeze.frozen_retrieval():
        queue.put({"error": "the active index is not the frozen configuration"})
        return
    pack = seal.open_sealed(REPO / "eval" / "locked.json.age", "T38 locked final evaluation")
    questions = pack["questions"]
    facts_file = json.loads((REPO / "data" / "facts.json").read_text())
    facts = {f["id"]: f for f in facts_file["facts"]}
    deps, config = run_eval.setup(selected["retrieval"]["reranker"] == "on", judge=True)
    outcome = eval_run.run(
        questions, facts, deps, config, split="locked", stage="final", locked_run=True
    )
    unsourced: dict[str, int] = {}
    for record in outcome.records:
        if record.get("unsourced_shown"):
            unsourced[record["language"]] = unsourced.get(record["language"], 0) + 1
    totals = {
        lang: sum(1 for r in outcome.records if r["language"] == lang) for lang in ("en", "es")
    }
    queue.put(
        {
            "run_id": outcome.run_id,
            "rows": [
                r for r in outcome.rows if r["metric"] not in ("faithfulness", "answer_relevancy")
            ],
            "blockers": len(outcome.blockers),
            "judge": [
                (r["language"], r["judge_input"]) for r in outcome.records if r.get("judge_input")
            ],
            "unsourced": unsourced,
            "totals": {**totals, "all": len(outcome.records)},
        }
    )


def main() -> int:
    from company_brain import dashboard, eval_run, final_eval, freeze

    if final_eval.locked_runs(eval_run.RUNS_CSV):
        print("The locked final evaluation has already run; it is never re-run.")
        return 1
    if not freeze.SELECTED.exists():
        print("Freeze the configuration first (scripts/freeze.py).")
        return 1
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    child = ctx.Process(target=answer_locked, args=(queue,))
    child.start()
    result = queue.get()
    child.join()
    if "error" in result:
        print(result["error"])
        return 1
    print(f"answered; judging {len(result['judge'])} answers", flush=True)
    import ollama

    from company_brain.embedding import ollama_url

    judge = eval_run.ragas_judge(ollama_url())
    scored = []
    for number, (language, judge_input) in enumerate(result["judge"], start=1):
        try:
            scored.append((language, dict(judge([judge_input])[0])))
        except Exception as error:  # noqa: BLE001 - a failed judgement is counted as missing
            print(f"judge failed on answer {number}: {type(error).__name__}", flush=True)
        print(f"judged {number} of {len(result['judge'])}", flush=True)
    ollama.Client(host=ollama_url()).generate(model=eval_run.JUDGE_MODEL, prompt="", keep_alive=0)
    date = f"{datetime.now():%Y-%m-%d %H:%M}"
    base = {
        "run_id": result["run_id"],
        "date": date,
        "split": "locked",
        "stage": "final",
        "config": f'{{"selected_sha256": "{freeze.sha256(freeze.SELECTED)}"}}',
    }
    rows = [{**r, "split": "locked", "stage": "final"} for r in result["rows"]]
    rows += final_eval.judge_rows(scored, base, result["totals"])
    rows += final_eval.citation_rows(result["unsourced"], result["totals"], base)
    text = final_eval.report(
        rows,
        result["blockers"],
        freeze.sha256(freeze.SELECTED),
        freeze.sha256(final_eval.LOCKED),
        result["run_id"],
        date,
    )
    final_eval.REPORT.write_text(text, encoding="utf-8")  # first, so a later failure loses nothing
    eval_run.append_rows(rows, locked_run=True)
    dashboard.rebuild_after("final evaluation")
    print(text)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
