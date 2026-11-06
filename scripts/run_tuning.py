"""Run T35's staged tuning on the tuning questions. Close Chrome first.

Usage: uv run python scripts/run_tuning.py estimate          (T35.AC4, before stage 1)
       uv run python scripts/run_tuning.py index MODEL BUDGET (a candidate index, not activated)
       uv run python scripts/run_tuning.py stage1|stage2|stage3
       uv run python scripts/run_tuning.py floors            (stage 4: relevance floor per language)

Stages 1 to 3 are retrieval only (`company_brain.tuning`); each appends its rows to
reports/eval_runs.csv and writes reports/tuning/<stage>.json with every result, the
winner and the seal access log's length before and after (T35.AC2). Stage 4's answers
run with `scripts/run_eval.py --stage stage4` once the winning index is active.
"""

import json
import os
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from company_brain import eval_run, rerank, retrieval, tuning  # noqa: E402
from company_brain.content_guard import GuardedReader, Manifest  # noqa: E402
from company_brain.convert import Docling, convert  # noqa: E402
from company_brain.db import connect  # noqa: E402
from company_brain.drive import load_sources  # noqa: E402
from company_brain.embedding import EmbeddingClient, index_id_for, load_embedding_models  # noqa: E402
from company_brain.indexing import index_documents  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "reports" / "tuning"
SEAL_LOG = REPO / "reports" / "seal_access.log"
MODELS = ["qwen3-embedding:0.6b", "bge-m3:latest", "embeddinggemma-2:270m"]
BUDGETS = [300, 500, 800]
BASELINE = {"embedding": "bge-m3:latest", "budget": 500, "reranker": "on"}


def digest(model: str) -> str:
    models = tomllib.loads((REPO / "config" / "models.toml").read_text(encoding="utf-8"))
    return str(models["ollama"][model]["digest"])


class Bench:
    """The tuning questions, their required spans and a search for any configuration."""

    def __init__(self) -> None:
        self.questions = json.loads((REPO / "eval" / "tuning.json").read_text())["questions"]
        self.facts = {
            f["id"]: f for f in json.loads((REPO / "data" / "facts.json").read_text())["facts"]
        }
        self.root = load_sources(REPO / "config" / "sources.toml")["root"]
        self.reader = GuardedReader(self.root, Manifest.load(REPO / "data" / "manifests"))
        self.docling = Docling()
        self.cache: dict[str, Any] = {}
        self.conn = connect()
        self.embedders: dict[str, EmbeddingClient] = {}
        self.reranker = rerank.Reranker()

    def convert(self, relative: str) -> Any:
        if relative not in self.cache:
            self.cache[relative] = convert(self.reader.read(self.root / relative), self.docling)
        return self.cache[relative]

    def spans(self, question: dict[str, Any]) -> list[str]:
        return eval_run.span_texts(question, self.facts, self.convert)

    def hits(self, config: dict[str, Any], question: str, scope: str) -> list[retrieval.Hit]:
        model = config["embedding"]
        if model not in self.embedders:
            self.embedders[model] = EmbeddingClient(load_embedding_models()[model])
        index = index_id_for(model, digest(model), config["budget"])
        found = retrieval.retrieve(
            self.conn,
            self.embedders[model].embed_query(question),
            question,
            scope,
            scope,
            index_id=index,
        )
        self.conn.commit()
        return found

    def ordered(self, config: dict[str, Any], question: str, scope: str) -> list[str]:
        """The passages in the order the product uses: the reranker's when it is on."""
        hits = self.hits(config, question, scope)
        if config["reranker"] == "on" and hits:
            head = hits[:40]
            values = self.reranker.scores(question, [f"{h.label}\n{h.text}" for h in head])
            pairs = sorted(zip(head, values, strict=True), key=lambda p: (-p[1], p[0].chunk_id))
            hits = [h for h, _v in pairs] + hits[40:]
        return [f"{h.label}\n{h.text}" for h in hits]

    def stage(self, name: str, configs: list[dict[str, Any]]) -> dict[str, Any]:
        seal_before = SEAL_LOG.read_text(encoding="utf-8").count("\n")
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
        results = []
        for config in configs:
            searched = tuning.search_all(
                self.questions, self.spans, lambda q, s, c=config: self.ordered(c, q, s)
            )
            result = tuning.score(config, searched)
            eval_run.append_rows(tuning.rows(run_id, name, result))
            print(
                f"{name} {config}: recall@5 {result.recall:.3f}, MRR {result.mrr:.3f}, "
                f"{result.seconds:.2f} s",
                flush=True,
            )
            results.append(result)
        best = tuning.winner(results)
        record = {
            "stage": name,
            "run_id": run_id,
            "results": [
                {
                    "config": r.config,
                    "recall@5": r.recall,
                    "mrr": r.mrr,
                    "seconds": r.seconds,
                    "by_language": r.by_language,
                    "counts": r.counts,
                }
                for r in results
            ],
            "winner": best.config,
            "seal_log_lines": [seal_before, SEAL_LOG.read_text(encoding="utf-8").count("\n")],
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{name}.json").write_text(json.dumps(record, indent=1) + "\n")
        return record


def winner_of(stage: str) -> dict[str, Any]:
    return dict(json.loads((OUT / f"{stage}.json").read_text())["winner"])


def estimate() -> int:
    speeds = {}
    for line in (REPO / "reports" / "embedding_speed.csv").read_text().splitlines()[1:]:
        model, _dims, seconds = line.split(",")
        speeds[model] = float(seconds)
    conn = connect()
    chunks = conn.execute(
        "SELECT count(*) FROM brain.chunks c JOIN brain.index_meta m USING (index_id) "
        "WHERE m.active"
    ).fetchone()[0]
    conn.close()
    missing = [m for m in MODELS if m != BASELINE["embedding"]]
    # T24: indexing the 77 test documents with bge-m3 took 11 minutes, most of it conversion.
    record = tuning.stage1_estimate(chunks, speeds, 11.0, missing)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stage1_estimate.json").write_text(json.dumps(record, indent=1) + "\n")
    print(record)
    return 0


def floors() -> int:
    config = winner_of("stage3")
    bench = Bench()
    scored: dict[str, list[tuple[float, bool]]] = {"en": [], "es": []}
    for q in bench.questions:
        if q["mode"] == "summarise":
            continue
        scope = f"fictional:{q['client']}"
        hits = bench.hits(config, q["question"], scope)[:40]
        values = bench.reranker.scores(q["question"], [f"{h.label}\n{h.text}" for h in hits])
        scored[q["language"]].append((max(values, default=-1e9), q["category"] != "unanswerable"))
    candidates = [x / 2 for x in range(-20, 21)]
    chosen = {lang: tuning.threshold_choice(pairs, candidates) for lang, pairs in scored.items()}
    (OUT / "floors.json").write_text(
        json.dumps({"config": config, "scored": scored, "chosen": chosen}, indent=1) + "\n"
    )
    path = REPO / "config" / "relevance.toml"
    head = path.read_text(encoding="utf-8").split("[threshold]")[0]
    body = "".join(f"{lang} = {value}\n" for lang, value in chosen.items())
    path.write_text(f"{head}[threshold]\n{body}", encoding="utf-8")
    print(chosen)
    return 0


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command == "estimate":
        return estimate()
    if command == "index":
        result = index_documents(model_name=argv[1], budget=int(argv[2]), activate=False)
        print(result)
        return 0 if result.state == "completed" else 1
    if command == "stage1":
        record = json.loads((OUT / "stage1_estimate.json").read_text())
        models = MODELS if record["keep_embeddinggemma"] else MODELS[:2]
        Bench().stage("stage1", [{**BASELINE, "embedding": m} for m in models])
        return 0
    if command == "stage2":
        best = winner_of("stage1")
        Bench().stage("stage2", [{**best, "budget": b} for b in BUDGETS])
        return 0
    if command == "stage3":
        best = winner_of("stage2")
        Bench().stage("stage3", [{**best, "reranker": r} for r in ("on", "off")])
        return 0
    if command == "floors":
        return floors()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
