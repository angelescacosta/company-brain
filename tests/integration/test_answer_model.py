"""T24.AC1 and AC2 with the answering model (Qwen3.5-4B, local Ollama).

Five fixture questions in English and Spanish, each with three fictional
sources; every fact must cite only provided sources, and the answer must be in
the question's language. The model is unloaded afterwards to free memory.
"""

import json

import pytest

pytestmark = pytest.mark.integration

CASES = [
    # Tuning clients only: nothing is tuned on the locked clients (T14).
    (
        "What fee did we agree with Pellmore Dental Group?",
        [
            "Consulting agreement (signed), clause 3: the client pays a fixed fee of EUR 36,500.",
            "Consulting agreement draft v1, clause 3: the client pays a fixed fee of EUR 33,000.",
            "Call 27 August: text message reminders replace postal reminders.",
        ],
    ),
    (
        "Which clinics are in the first phase of the Pellmore project?",
        [
            "Call 27 August: the first phase covers the Solihull and Dudley clinics.",
            "Progress report: missed appointments in Solihull fell from 11% to 7%.",
            "Proposal: the project takes 10 weeks.",
        ],
    ),
    (
        "What is the notice period in the Quillstone Hotels agreement?",
        [
            "Consulting agreement (signed), clause 6: "
            "either party may end it with 45 days' notice.",
            "Proposal: option 2 costs EUR 62,000.",
            "Call 20 October: Durham and Lincoln join in January 2027.",
        ],
    ),
    (
        "¿Cuál es el plazo de pago del contrato con Conservas Arrieta?",
        [
            "Contrato de consultoría (firmado), cláusula 4: "
            "el cliente paga en un plazo de 60 días.",
            "Informe de avance: el error medio de la previsión bajó del 25 % al 12 %.",
            "Llamada 31 de agosto: las reuniones de seguimiento son los jueves a las 11:00.",
        ],
    ),
    (
        "¿Cuántas horas se tarda ahora en enviar un presupuesto en Talleres Benamar?",
        [
            "Informe de avance: las horas hasta el envío del presupuesto bajaron de 72 a 5.",
            "Informe de avance: las reparaciones en plazo subieron del 74 % al 86 %.",
            "Propuesta: el proyecto dura 14 semanas.",
        ],
    ),
]

CHECK = """
import json
import ollama
import company_brain.answer as answer
from company_brain.rerank import Selection, language_of
from company_brain.retrieval import Hit
out = []
for question, texts in {cases!r}:
    hits = []
    for n, t in enumerate(texts):
        found = Hit(f"c{{n}}", f"d{{n}}", "A", "A", f"x/{{n}}", t, "Client | file", None, (), 0.0)
        hits.append((found, 5.0 - n))
    result = answer.answer(question, Selection(kept=tuple(hits)), answer.ollama_generate)
    out.append({{
        "q": question, "text": result.text, "problems": answer.citation_problems(result),
        "lang": language_of(result.text), "qlang": result.language,
        "facts": len(result.facts), "seconds": result.timings.get("generate_s"),
    }})
client = ollama.Client(host="http://127.0.0.1:11434")
client.generate(model=answer.ANSWER_MODEL, prompt="", keep_alive=0)
print(json.dumps(out, ensure_ascii=False))
"""


def test_citations_and_language(run_offline) -> None:  # type: ignore[no-untyped-def]
    result = run_offline(CHECK.format(cases=CASES))
    assert result.returncode == 0, result.stderr[-2000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    for row in out:
        assert row["facts"] >= 1, row
        assert row["problems"] == [], row
        assert row["lang"] == row["qlang"], row
    assert "36,500" in out[0]["text"] and "5" in out[4]["text"]
