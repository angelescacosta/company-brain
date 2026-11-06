"""Run the early model benchmark (T7) against the running Ollama service.

Loads the embedding model in Ollama and the reranker in this process and keeps
both resident, then measures the answering model (revision 9: Qwen3.5-4B only),
with memory pressure sampled throughout. Close Chrome first. Writes
reports/benchmark_early.csv and config/roster.toml. Takes about 15 minutes.
"""

import os
import sys
import time
import tomllib
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import ollama  # noqa: E402
from sentence_transformers import CrossEncoder  # noqa: E402

from company_brain.benchmark import (  # noqa: E402
    ANSWER_MODEL,
    EMBEDDING_MODEL,
    OPTIONS,
    benchmark_model,
    prompts,
    settle,
    write_csv,
    write_roster,
)

REPO = Path(__file__).resolve().parents[1]
RERANKER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"  # D31


def unload_all(client: ollama.Client, keep: str | None = None) -> None:
    """Unload every model Ollama holds (except `keep`), so each measurement starts clean."""
    for running in client.ps().models:
        if running.model != keep:
            client.generate(model=running.model, prompt="", keep_alive=0)


def main() -> int:
    client = ollama.Client(host="127.0.0.1:11434")
    unload_all(client)
    idle = settle()
    print(f"idle memory used {idle / 1e9:.1f} GB", flush=True)
    models = tomllib.loads((REPO / "config" / "models.toml").read_text())
    reranker = CrossEncoder(RERANKER, revision=models["huggingface"][RERANKER]["revision"])
    reranker.predict([("warm", "up")])
    client.embed(model=EMBEDDING_MODEL, input="warm up", keep_alive=-1)

    def generate(model: str, text: str) -> tuple[float, float]:
        began = time.monotonic()
        first = None
        for chunk in client.chat(
            model=model,
            messages=[{"role": "user", "content": text}],
            stream=True,
            think=False,
            options=OPTIONS,
            keep_alive="30m",
        ):
            if first is None and chunk.message.content:
                first = time.monotonic() - began
        total = time.monotonic() - began
        return (first if first is not None else total), total

    table = []
    items = prompts()
    for model in (ANSWER_MODEL,):
        unload_all(client, keep=EMBEDDING_MODEL)
        settle()
        print(f"{time.strftime('%H:%M')} measuring {model}", flush=True)
        row = benchmark_model(model, generate, items, idle_memory=idle)
        print(row, flush=True)
        table.append(row)
    write_csv(REPO / "reports" / "benchmark_early.csv", table)
    write_roster(REPO / "config" / "roster.toml", table)
    unload_all(client)
    del reranker
    return 0


if __name__ == "__main__":
    sys.exit(main())
