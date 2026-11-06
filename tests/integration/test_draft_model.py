"""T27 with the answering model: four fixture drafts for the tuning clients.

AC1: no shown fact carries a value missing from its sources. AC2: agreed terms
not in the sources are asked as questions. AC3: the methodology's structure.
"""

import json

import pytest

pytestmark = pytest.mark.integration

METHOD = (
    "Methodology: each option has its own fixed fee, paid in equal monthly instalments, "
    "each in advance; after the options an optional monthly retainer, paid in advance, "
    "only for questions and support in meetings."
)
CASES = [
    (
        "Draft a proposal for Pellmore Dental Group.",
        [
            "Ravi Senthil: we lost almost eleven percent of our chair time last quarter.",
            "Agreed: the first phase covers the Solihull and Dudley clinics.",
            "Agreed: text message reminders replace postal reminders.",
        ],
    ),
    (
        "Draft a proposal for Quillstone Hotels.",
        [
            "Call 1 September: Lucía Ferrer: York and Harrogate are the two pilot hotels.",
            "Call 1 September: Tom Hartley: We review prices once a month at best.",
            "Call 20 October: Durham and Lincoln join in January 2027.",
        ],
    ),
    (
        "Prepara una propuesta para Conservas Arrieta.",
        [
            "Llamada 31 de agosto: Iñaki Arrieta: el error de la previsión ronda el 25 %.",
            "Acordado: la previsión empieza por la familia de atún en aceite.",
            "Llamada 31 de agosto: tenemos cuatro meses de producto terminado en almacén.",
        ],
    ),
    (
        "Prepara una propuesta para Talleres Benamar.",
        [
            "Llamada 4 de septiembre: Rocío Benamar: tardamos 72 horas en enviar un presupuesto.",
            "Llamada 4 de septiembre: Acordado: el piloto se hace en Teatinos y El Palo.",
            "Llamada 4 de septiembre: Acordado: los presupuestos se envían por mensaje con fotos.",
        ],
    ),
]

CHECK = """
import json
import ollama
import company_brain.draft as draft
import company_brain.guardrails as guardrails
from company_brain.rerank import Selection
from company_brain.retrieval import Hit

out = []
for request, texts in {cases!r}:
    texts = texts + [{method!r}]
    hits = []
    for n, t in enumerate(texts):
        path = "Methodology/Methodology (EN).docx" if t.startswith("Methodology") else f"x/{{n}}"
        hits.append((Hit(f"c{{n}}", f"d{{n}}", "A", "A", path, t, "file", None, (), 0.0), 5.0 - n))
    result = draft.draft(request, Selection(kept=tuple(hits)), draft.ollama_generate)
    sources = {{i + 1: t for i, t in enumerate(texts)}}
    unsourced = []
    for line in result.facts_text.splitlines():
        cited = guardrails.parse_citations(line)
        if cited and guardrails.NOT_IN_SOURCES not in line:
            unsourced += guardrails.check_fact(line, cited, sources, "en").failed_values
    out.append({{"text": result.text, "questions": result.questions, "unsourced": unsourced,
                 "readable": result.readable}})
client = ollama.Client(host="http://127.0.0.1:11434")
client.generate(model=draft.ANSWER_MODEL, prompt="", keep_alive=0)
print(json.dumps(out, ensure_ascii=False))
"""


def test_four_drafts(run_offline) -> None:  # type: ignore[no-untyped-def]
    from company_brain.draft import HEADINGS

    result = run_offline(CHECK.format(cases=CASES, method=METHOD), timeout=1800)
    assert result.returncode == 0, result.stderr[-2000:]
    drafts = json.loads(result.stdout.strip().splitlines()[-1])
    for (request, _texts), out in zip(CASES, drafts, strict=True):
        assert out["readable"], request
        assert out["unsourced"] == [], (request, out["unsourced"])  # AC1
        assert out["questions"], request  # AC2: no fee or start date is agreed in these sources
        language = "es" if request.startswith("Prepara") else "en"
        headings = [line[3:] for line in out["text"].splitlines() if line.startswith("## ")]
        assert headings == HEADINGS[language], (request, headings)  # AC3
