"""T21.AC4: cross-language retrieval and exact names on the fictional corpus.

Runs in a child process with the real bge-m3 embeddings (local Ollama) on the
test database. PDFs and images are left out (they need Docling and are covered
by T17); transcripts, Word files and spreadsheets are ingested.
"""

import json

import pytest

pytestmark = pytest.mark.integration

# The child imports modules as `company_brain.x` (never `from company_brain import`), which
# the isolation guard would read as the real database name and refuse.
CHECK = """
import json, tempfile
from pathlib import Path
from psycopg import sql
import company_brain.corpus as corpus
import company_brain.ingest as ingest
import company_brain.retrieval as retrieval
from company_brain.content_guard import GuardedReader, Manifest
from company_brain.db import connect, migrate
from company_brain.embedding import EmbeddingClient, load_embedding_models, register_index

conn = connect()
tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'brain'").fetchall()
for (table,) in tables:
    conn.execute(sql.SQL("DROP TABLE IF EXISTS brain.{} CASCADE").format(sql.Identifier(table)))
conn.commit(); migrate(conn); conn.commit()
root = Path(tempfile.mkdtemp())
corpus.build(root / corpus.PREFIX)
reader = GuardedReader(root, Manifest.load(Path("data/manifests")))
model = load_embedding_models()["bge-m3:latest"]
client = EmbeddingClient(model)
dims = len(client.embed_query("x"))
index_id = register_index(conn, model, digest="test", dimensions=dims, chunk_budget=500)
conn.commit()
target = ingest.Target(index_id, 500, model, client.embed_documents)
quick = lambda p: p.suffix.lower() in {".vtt", ".docx", ".xlsx"}
result = ingest.ingest(conn, root, [target], lambda p: reader.read(p), is_listed=quick)
conn.execute(
    "UPDATE brain.index_meta SET active = true, complete = true WHERE index_id = %s",
    (index_id,),
)
conn.commit()
out = {"added": result.added}
for name, scope, question in [
    ("en_to_es", "fictional:Quillstone Hotels",
     "Who will send the current rates of the four hotels?"),
    ("es_to_en", "fictional:Quillstone Hotels", "¿Qué dos hoteles forman parte del piloto?"),
    ("name", "fictional:Quillstone Hotels", "Lucía Ferrer"),
    ("clause", "fictional:Harrowgate Freight", "clause 4 payment terms"),
    ("clausula", "fictional:Talleres Benamar", "cláusula 4 plazo de pago"),
]:
    vector = client.embed_query(question)
    hits = retrieval.retrieve(conn, vector, question, scope=scope, chat_scope=scope)
    out[name] = [h.text for h in hits[:5]]
print(json.dumps(out, ensure_ascii=False))
"""


def test_cross_language_names_and_clauses(run_offline) -> None:  # type: ignore[no-untyped-def]
    result = run_offline(CHECK)
    assert result.returncode == 0, result.stderr[-3000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["added"] > 20
    assert any("tarifas actuales de los cuatro hoteles" in t for t in out["en_to_es"]), out[
        "en_to_es"
    ]
    assert any("York and Harrogate" in t for t in out["es_to_en"]), out["es_to_en"]
    assert any("Lucía Ferrer" in t for t in out["name"][:3]), out["name"]
    assert any("30 days" in t for t in out["clause"]), out["clause"]
    assert any("30 días" in t for t in out["clausula"]), out["clausula"]
