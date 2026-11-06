"""Time Answer mode on 5 tuning questions and write reports/runtime_estimate.md (T24.AC4).

Usage: uv run python scripts/runtime_estimate.py

Needs the active index (scripts/update_index.py) and Ollama running. Each
question goes through search, reranking and the answering model; one judge
call is timed too. The projection for T35 is in company_brain.runtime_estimate.
"""

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import ollama

from company_brain import answer, guardrails, rerank, retrieval
from company_brain.benchmark import PRESSURE_NAMES, Sampler, pressure_verdict
from company_brain.db import connect
from company_brain.embedding import EmbeddingClient, load_embedding_models
from company_brain.runtime_estimate import FIT_HOURS, Timings, project

REPO = Path(__file__).resolve().parents[1]
EMBED_S_PER_CHUNK = 0.552 / 20  # bge-m3, reports/embedding_speed.csv (T19)


def main() -> int:
    pack = json.loads((REPO / "eval" / "tuning.json").read_text(encoding="utf-8"))["questions"]
    chosen = [q for q in pack if q["mode"] == "answer" and q["category"] != "unanswerable"]
    chosen = [q for q in chosen if q["language"] == "en"][:3] + [
        q for q in chosen if q["language"] == "es"
    ][:2]
    client = EmbeddingClient(load_embedding_models()["bge-m3:latest"])
    reranker = rerank.Reranker()
    claim_score = guardrails.hhem_scorer()
    rows = []
    sampler = Sampler()
    sampler.__enter__()
    with connect() as conn:
        run = conn.execute(
            "SELECT extract(epoch FROM ended_at - started_at) FROM brain.ingest_runs "
            "WHERE state = 'completed' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        chunks = conn.execute(
            "SELECT count(*) FROM brain.chunks c JOIN brain.index_meta m "
            "USING (index_id) WHERE m.active"
        ).fetchone()
        for q in chosen:
            scope = f"fictional:{q['client']}"
            start = time.monotonic()
            hits = retrieval.retrieve(
                conn,
                client.embed_query(q["question"]),
                q["question"],
                scope=scope,
                chat_scope=scope,
            )
            searched = time.monotonic()
            selection = reranker.rerank(q["question"], hits[:40])
            ranked = time.monotonic()
            result = answer.answer(q["question"], selection, answer.ollama_generate)
            generated = time.monotonic()
            gated = guardrails.gate(result, claim_score=claim_score)
            done = time.monotonic()
            rows.append(
                {
                    "id": q["id"],
                    "retrieve": searched - start,
                    "rerank": ranked - searched,
                    "generate": generated - ranked,
                    "gate": done - generated,
                    "total": done - start,
                    "warnings": len(gated.warnings),
                    "facts": len(result.facts),
                    "problems": answer.citation_problems(result),
                }
            )
            print(rows[-1], flush=True)
    judge_start = time.monotonic()
    ollama.Client(host="http://127.0.0.1:11434").chat(
        model=answer.ANSWER_MODEL,
        think=False,
        options={"temperature": 0, "num_predict": 200},
        messages=[
            {
                "role": "user",
                "content": "Source: The fee is EUR 48,000.\nClaim: The fee is "
                "EUR 48,000.\nList each statement in the claim and say if the source supports it.",
            }
        ],
    )
    judge = time.monotonic() - judge_start
    sampler.__exit__()
    verdict = pressure_verdict(sampler.trace)
    peak = max((level or 0 for _t, level in sampler.trace), default=0)
    chrome = (
        "was open"
        if subprocess.run(["/usr/bin/pgrep", "-x", "Google Chrome"], capture_output=True).returncode
        == 0
        else "was closed"
    )  # noqa: S603
    meets = verdict == "pass" and max(r["total"] for r in rows) <= 60
    total_ingest = float(run[0]) if run else 0.0
    count = int(chunks[0]) if chunks else 0
    timings = Timings(
        answer_s=statistics.median(r["total"] for r in rows),
        judge_s=judge,
        retrieve_s=statistics.median(r["retrieve"] for r in rows),
        rerank_s=statistics.median(r["rerank"] for r in rows),
        convert_s=max(0.0, total_ingest - EMBED_S_PER_CHUNK * count),
        embed_s_per_chunk=EMBED_S_PER_CHUNK,
        chunks=count,
    )
    plan = project(timings)
    lines = [
        "# Preliminary runtime estimate (T24.AC4)",
        "",
        f"Measured {time.strftime('%Y-%m-%d %H:%M')} with Qwen3.5-4B, bge-m3 and the mMiniLMv2 "
        "reranker on the active index, including the output gate and its claim scores (T25).",
        "",
        "| Measure | Seconds |",
        "| --- | --- |",
        f"| Answer (median), search to checked answer | {timings.answer_s:.1f} |",
        f"| Output gate with claim scores (median) | "
        f"{statistics.median(r['gate'] for r in rows):.2f} |",
        f"| Search (median) | {timings.retrieve_s:.2f} |",
        f"| Reranking (median) | {timings.rerank_s:.2f} |",
        f"| One judge call | {timings.judge_s:.1f} |",
        f"| Converting every document once | {timings.convert_s:.0f} |",
        f"| Chunks in the index | {timings.chunks} |",
        "",
        "| T35 stage | Hours |",
        "| --- | --- |",
        *[f"| {name} | {hours:.2f} |" for name, hours in plan.stages.items()],
        f"| Total | {plan.total_hours:.2f} |",
        "",
        f"Fits by 31 October: {'yes' if not plan.reduced else 'no'} (limit {FIT_HOURS:.0f} hours "
        "of machine time, so the runs fit in the nights of 28 to 30 October).",
    ]
    if plan.reduced:
        lines.append("The reduced plan applies: stage 4 on the 20-question set.")
    lines += [
        "",
        f"Recovery checkpoint (T24.AC5): Qwen3.5-4B meets T33's targets on these questions: "
        f"{'yes' if meets else 'no'} (slowest answer {max(r['total'] for r in rows):.1f} s against "
        f"60 s; memory pressure {verdict}, highest level "
        f"{PRESSURE_NAMES.get(peak, 'unknown')}, {len(sampler.trace)} samples). "
        f"Measured with the owner's usual apps open; Chrome {chrome}.",
    ]
    lines += [
        "",
        "Per question:",
        "",
        "| Question | Search | Rerank | Model | Gate | Total | Facts | Citation problems "
        "| Warnings |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {r['id']} | {r['retrieve']:.2f} | {r['rerank']:.2f} | {r['generate']:.1f} | "
        f"{r['gate']:.2f} | {r['total']:.1f} | {r['facts']} | {len(r['problems'])} | "
        f"{r['warnings']} |"
        for r in rows
    ]
    (REPO / "reports" / "runtime_estimate.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ollama.Client(host="http://127.0.0.1:11434").generate(
        model=answer.ANSWER_MODEL, prompt="", keep_alive=0
    )
    print("\n".join(lines[:22]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
