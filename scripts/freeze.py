"""Freeze the configuration (T37): write config/selected.toml, then make its index active.

Usage: uv run python scripts/freeze.py [--no-activate]

Retrieval: T35 stage 1 and 2 winners; reranker kept on (D42). Answering model: the
T37 policy over the tuning run made with exactly that retrieval, the owner's blind
scores (H6) and the full benchmark (T33). Activating runs a normal document update
with the frozen embedding model and budget, which reuses that index and activates it.
"""

import csv
import json
import statistics
import sys
import tomllib
from pathlib import Path

from company_brain import freeze, rerank
from company_brain.answer import ANSWER_MODEL

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "reports" / "eval_runs.csv"
RATINGS = REPO / "reports" / "blind_ratings_unblinded.csv"


def tuning_run(embedding: str, budget: int, reranker: str) -> str:
    """The latest stage 4 run made with exactly the frozen retrieval."""
    found = ""
    with RUNS.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["stage"] != "stage4" or row["metric"] != "hard_blockers":
                continue
            config = json.loads(row["config"])
            if (config.get("embedding"), config.get("budget"), config.get("reranker")) == (
                embedding,
                budget,
                reranker,
            ):
                found = row["run_id"]
    if not found:
        raise SystemExit("no stage 4 run with the frozen retrieval settings")
    return found


def answer_latency() -> float:
    with (REPO / "reports" / "benchmark_full.csv").open(encoding="utf-8") as handle:
        warm = [
            float(r["total_s"])
            for r in csv.DictReader(handle)
            if r["mode"] == "answer" and r["cold"] == "no" and r["model"] == ANSWER_MODEL
        ]
    return statistics.median(warm)


def main(argv: list[str]) -> int:
    stages = [json.loads((REPO / f"reports/tuning/stage{n}.json").read_text()) for n in (1, 2)]
    embedding = freeze.stage_winner(stages[0])["embedding"]
    budget = int(freeze.stage_winner(stages[1])["budget"])
    reranker = "on"  # D42
    run_id = tuning_run(embedding, budget, reranker)
    roster = tomllib.loads((REPO / "config" / "roster.toml").read_text())
    models = tomllib.loads((REPO / "config" / "models.toml").read_text())["ollama"]
    thresholds = tomllib.loads((REPO / "config" / "relevance.toml").read_text())["threshold"]
    scores = freeze.mean_scores(RATINGS)
    candidate = freeze.Candidate(
        model=ANSWER_MODEL,
        hard_blockers=freeze.blocker_count(RUNS, run_id),
        meets_targets=roster["full_benchmark"]["result"] == "pass",
        mean_score=scores.get(ANSWER_MODEL),
        warnings=0,
        latency_s=answer_latency(),
    )
    status, model = freeze.choose(
        [candidate], single_model=roster["roster"]["path"] == "single-model"
    )
    if model is None:
        print(f"Freeze: {status}")
        return 1
    tables = {
        "retrieval": {
            "embedding": embedding,
            "embedding_digest": models[embedding]["digest"],
            "budget": budget,
            "reranker": reranker,
            "reranker_model": rerank.MODEL,
            "threshold_en": float(thresholds["en"]),
            "threshold_es": float(thresholds["es"]),
        },
        "answer": {
            "model": model,
            "model_digest": models[model]["digest"],
            "status": status,
            "mean_owner_score": round(candidate.mean_score or 0.0, 2),
            "hard_blockers": candidate.hard_blockers,
            "meets_targets": candidate.meets_targets,
            "latency_s": round(candidate.latency_s, 2),
        },
        "evidence": {
            "frozen": freeze.now(),
            "tuning_run": run_id,
            "stage_runs": [s["run_id"] for s in stages],
            "ratings_sha256": freeze.sha256(RATINGS),
            "models_toml_sha256": freeze.sha256(REPO / "config" / "models.toml"),
            "relevance_toml_sha256": freeze.sha256(REPO / "config" / "relevance.toml"),
            "versions_toml_sha256": freeze.sha256(REPO / "config" / "versions.toml"),
            "benchmark": "reports/benchmark_full.csv (bge-m3, the largest embedding: worst case)",
        },
    }
    header = "Frozen configuration (T37). Written by scripts/freeze.py; do not edit by hand."
    freeze.SELECTED.write_text(freeze.to_toml(tables, header), encoding="utf-8")
    print(f"Frozen: {model} ({status}), {embedding}, budget {budget}, reranker {reranker}")
    if "--no-activate" not in argv:
        from company_brain.indexing import index_documents

        result = index_documents(trigger="manual")
        print(f"Index update for the frozen settings: {result.state}")
        if result.state != "completed":
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
