"""Run the evaluation on the tuning questions (T34) and append the results.

Usage: uv run python scripts/run_eval.py [--reranker on|off] [--no-judge] [--limit N]
                                         [--stage NAME]
       uv run python scripts/run_eval.py --judge RUN_ID   (judge a saved run; resumable)

Uses the active index, Qwen3.5-4B, the output gate with HHEM, and Gemma 4 12B
as the judge after the answering model is unloaded. Close Chrome first: the
judge is a larger model.
"""

import argparse
import gc
import json
import os
import sys
from pathlib import Path

import ollama

from company_brain import answer, egress, eval_run, guardrails, rerank, retrieval, summarise
from company_brain.content_guard import GuardedReader, Manifest
from company_brain.convert import Docling, convert
from company_brain.db import connect
from company_brain.drive import load_sources
from company_brain.embedding import EmbeddingClient, load_embedding_models, ollama_url

REPO = Path(__file__).resolve().parents[1]


def firewall() -> list[str]:
    python = os.path.realpath(sys.executable)
    problems = egress.lulu_problems({python: python})
    if egress.internet_reachable():
        from company_brain.firewall import verdict

        probes = egress.run_probes(ollama_url().removeprefix("http://"))
        if verdict(probes) != "pass":
            problems.append(f"a firewall probe did not pass: {verdict(probes)}")
    return problems


def setup(reranker_on: bool, judge: bool) -> tuple[eval_run.Deps, dict[str, object]]:
    """The evaluation dependencies on the active index (shared with T38's locked run)."""
    root = load_sources(REPO / "config" / "sources.toml")["root"]
    reader = GuardedReader(root, Manifest.load(REPO / "data" / "manifests"))
    docling = Docling()
    cache: dict[str, object] = {}

    def convert_file(relative: str):  # type: ignore[no-untyped-def]
        if relative not in cache:
            cache[relative] = convert(reader.read(root / relative), docling)
        return cache[relative]

    conn = connect()
    index = conn.execute(
        "SELECT index_id, model, chunk_budget FROM brain.index_meta WHERE active"
    ).fetchone()
    conn.commit()
    embedder = EmbeddingClient(load_embedding_models()[index[1]])
    reranker = rerank.Reranker()
    claim_score = guardrails.hhem_scorer()

    def retrieve(question: str, scope: str):  # type: ignore[no-untyped-def]
        hits = retrieval.retrieve(conn, embedder.embed_query(question), question, scope, scope)
        conn.commit()
        return hits

    def do_rerank(question: str, hits):  # type: ignore[no-untyped-def]
        pairs = retrieval.counterpart_map(conn, hits[0].scope, hits[0].scope) if hits else {}
        conn.commit()
        return reranker.rerank(question, hits, counterparts=pairs)

    def respond(question: str, selection):  # type: ignore[no-untyped-def]
        result = answer.answer(question, selection, answer.ollama_generate)
        return guardrails.gate(result, claim_score=claim_score), result

    def unload() -> None:
        nonlocal reranker, claim_score
        client = ollama.Client(host=ollama_url())
        client.generate(model=answer.ANSWER_MODEL, prompt="", keep_alive=0)
        del reranker, claim_score
        gc.collect()

    deps = eval_run.Deps(
        retrieve=retrieve,
        rerank=do_rerank if reranker_on else None,
        respond=respond,
        summarise=lambda c: summarise.summarise(c, summarise.ollama_generate),
        convert=convert_file,
        claim_score=claim_score,
        firewall=firewall,
        unload=unload,
    )
    config = {
        "index": index[0],
        "embedding": index[1],
        "budget": index[2],
        "reranker": "on" if reranker_on else "off",
        "answer_model": answer.ANSWER_MODEL,
        "judge": eval_run.JUDGE_MODEL if judge else None,
        "thresholds": rerank.thresholds(),
    }
    return deps, config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reranker", choices=("on", "off"), default="on")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--stage", default="manual")
    parser.add_argument("--judge", metavar="RUN_ID", help="only judge a saved run")
    args = parser.parse_args()
    if args.judge:
        folder = REPO / "reports" / "runs" / args.judge
        judge = eval_run.ragas_judge(ollama_url())
        count = eval_run.judge_saved(folder, judge, progress=lambda m: print(m, flush=True))
        client = ollama.Client(host=ollama_url())
        client.generate(model=eval_run.JUDGE_MODEL, prompt="", keep_alive=0)
        print(f"judged {count} answers; rows appended for run {args.judge}")
        return 0
    questions = json.loads((REPO / "eval" / "tuning.json").read_text())["questions"]
    if args.limit:
        questions = questions[: args.limit]
    facts = {f["id"]: f for f in json.loads((REPO / "data" / "facts.json").read_text())["facts"]}
    deps, config = setup(args.reranker == "on", judge=not args.no_judge)
    outcome = eval_run.run(questions, facts, deps, config, stage=args.stage)
    folder = eval_run.write(outcome)
    print("\n".join(eval_run.report_lines(outcome)))
    print(f"records: {folder}", flush=True)
    if not args.no_judge:
        # A fresh process for the judge: the answering models leave memory with this one.
        os.execv(sys.executable, [sys.executable, __file__, "--judge", outcome.run_id])  # noqa: S606
    return 0 if outcome.passed else 1


if __name__ == "__main__":
    sys.exit(main())
