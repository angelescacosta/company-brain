"""T19.AC5: each candidate embedding model passes an offline smoke test.

Checks that the configured prefixes reach Ollama (the client's vector equals
Ollama's own vector for the prefixed text), the dimension, finite unit
vectors, and records indexing speed on 20 chunks in reports/embedding_speed.csv.
"""

import csv
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "reports" / "embedding_speed.csv"
NAMES = ["qwen3-embedding:0.6b", "bge-m3:latest", "embeddinggemma-2:270m"]

SMOKE = """
import json, math, time, ollama
from company_brain.embedding import EmbeddingClient, load_embedding_models, normalise

name = {name!r}
model = load_embedding_models()[name]
raw = ollama.Client(host="http://127.0.0.1:11434")
sent = []

def backend(model_name, texts):
    sent.extend(texts)
    return raw.embed(model=model_name, input=texts).embeddings

client = EmbeddingClient(model, backend=backend, batch_size=8)
query = client.embed_query("¿Cuál es la tarifa acordada?")
assert sent == [model.query_prefix + "¿Cuál es la tarifa acordada?"]
direct = normalise(raw.embed(model=name, input=[sent[0]]).embeddings[0])
assert max(abs(a - b) for a, b in zip(query, direct)) < 1e-4, "prefix did not reach Ollama"
text = "The consultant invoices the agreed fee in two instalments."
chunks = [f"Clause {{i}}. {{text}}" for i in range(20)]
start = time.perf_counter()
vectors = client.embed_documents(chunks)
seconds = time.perf_counter() - start
assert len(sent) == 21 and all(t.startswith(model.document_prefix) for t in sent[1:])
dims = {{len(v) for v in vectors + [query]}}
assert len(dims) == 1
assert all(math.isfinite(x) for v in vectors for x in v)
assert all(abs(math.sqrt(sum(x * x for x in v)) - 1) < 1e-6 for v in vectors)
row = {{"model": name, "dimensions": dims.pop(), "seconds_20_chunks": round(seconds, 3)}}
print(json.dumps(row))
"""


@pytest.mark.parametrize("name", NAMES)
def test_embedding_model_smoke(run_offline, name: str) -> None:  # type: ignore[no-untyped-def]
    result = run_offline(SMOKE.format(name=name))
    assert result.returncode == 0, result.stderr[-2000:]
    row = json.loads(result.stdout.strip().splitlines()[-1])
    REPORT.parent.mkdir(exist_ok=True)
    rows = []
    if REPORT.exists():
        with REPORT.open() as handle:
            rows = [r for r in csv.DictReader(handle) if r["model"] != name]
    rows.append({k: str(v) for k, v in row.items()})
    with REPORT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["model", "dimensions", "seconds_20_chunks"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: r["model"]))
