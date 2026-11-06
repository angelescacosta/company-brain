"""T23: follow-up rewriting from the same chat only, and draft/signed labels.

AC1 runs with the real answering model (Qwen3.5-4B, local Ollama) on 10
fixture dialogues; the plumbing and AC2/AC3 use a stub.
"""

from hypothesis import given
from hypothesis import strategies as st

from company_brain import followups
from company_brain.followups import Turn
from company_brain.retrieval import Hit

DIALOGUES = [
    # Tuning clients only: nothing is tuned on the locked clients (T14).
    (
        "What fee did we agree with Pellmore Dental Group?",
        "EUR 36,500.",
        "And the notice period?",
        "Pellmore",
    ),
    (
        "Who is the main contact at Pellmore Dental Group?",
        "Ravi Senthil.",
        "When does their agreement start?",
        "Pellmore",
    ),
    (
        "Which hotels are in the Quillstone Hotels pilot?",
        "York and Harrogate.",
        "What was the average room rate in the first one?",
        "York",
    ),
    (
        "Summarise the Quillstone Hotels call of 20 October 2026.",
        "The pricing review moves to Mondays; Durham and Lincoln join in January 2027.",
        "Who runs the report workshop?",
        "Quillstone",
    ),
    (
        "What fee did the draft agreement with Quillstone Hotels state?",
        "EUR 58,500.",
        "And the signed one?",
        "Quillstone",
    ),
    (
        "¿Qué honorarios se firmaron con Conservas Arrieta?",
        "29.500 EUR.",
        "¿Y el plazo de pago?",
        "Arrieta",
    ),
    (
        "¿Quién es el contacto principal de Talleres Benamar?",
        "Rocío Benamar.",
        "¿Cuándo empieza su contrato?",
        "Benamar",
    ),
    (
        "¿Cuántas reparaciones hubo en julio en los talleres piloto de Talleres Benamar?",
        "365.",
        "¿Y en septiembre?",
        "Benamar",
    ),
    (
        "¿Qué se decidió en la llamada del 15 de octubre con Conservas Arrieta?",
        "La previsión se amplía al bonito en noviembre.",
        "¿Quién prepara el modelo de previsión?",
        "Arrieta",
    ),
    (
        "¿Cuánto tardaba Talleres Benamar en enviar un presupuesto al inicio?",
        "72 horas.",
        "¿Y ahora?",
        "presupuesto",
    ),
]

chat_ids = st.sampled_from(["c1", "c2", "c3"])
turns = st.lists(
    st.tuples(chat_ids, st.sampled_from(["user", "assistant"]), st.text(min_size=1, max_size=30)),
    max_size=30,
)


@given(turns, chat_ids)
def test_rewrite_input_holds_only_the_current_chat(
    log: list[tuple[str, str, str]], current: str
) -> None:
    """T23.AC2 PBT: no turn from another chat ever reaches the rewrite."""
    store = followups.ChatStore()
    for chat, role, text in log:
        store.add(chat, Turn(role, text))
    history = store.history(current)
    assert history == [Turn(r, t) for c, r, t in log if c == current]
    prompt = followups.rewrite_prompt("¿Y eso?", history)
    for chat, _role, text in log:
        if chat != current and all(text not in t.content for t in history):
            assert f"\n{text}\n" not in prompt


def test_first_question_is_not_rewritten() -> None:
    calls = []
    assert (
        followups.rewrite("What is the fee?", [], lambda p: calls.append(p) or "x")
        == "What is the fee?"
    )
    assert calls == []


def test_rewrite_uses_the_model_answer_trimmed() -> None:
    history = [
        Turn("user", "What fee did we agree with Pellmore Dental Group?"),
        Turn("assistant", "EUR 36,500."),
    ]
    out = followups.rewrite(
        "And the notice period?",
        history,
        lambda _p: '  "What is the notice period with Pellmore Dental Group?"\n',
    )
    assert out == "What is the notice period with Pellmore Dental Group?"


def test_draft_and_signed_are_both_labelled() -> None:
    """T23.AC3: when either version is retrieved, both carry their label."""

    def hit(cid: str, doc: str) -> Hit:
        return Hit(cid, doc, "A", "A", f"x/{doc}", "t", "label", None, (), 0.0)

    labels = followups.conflict_labels(
        [hit("c1", "signed")], [hit("c2", "draft")], {"signed": "signed", "draft": "draft"}
    )
    assert labels == {"c1": "SIGNED (current version)", "c2": "DRAFT (superseded)"}
    assert followups.conflict_labels([hit("c3", "report")], [], {"report": None}) == {}
