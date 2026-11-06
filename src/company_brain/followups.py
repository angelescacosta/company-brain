"""Follow-up rewriting and draft/signed labels (T23).

A follow-up is rewritten into a standalone question from the current chat's
own turns only (each chat is bound to one client, T29). The first question of
a chat is used as it is. Chunks from an agreement that has both a draft and a
signed version are labelled, so the answer can say which one is current.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from company_brain.benchmark import ANSWER_MODEL
from company_brain.retrieval import Hit

MAX_TURNS = 6  # the last three exchanges are enough to resolve "it", "the first one", "y"
PROMPT = (
    "Rewrite the user's last message as one standalone question that can be understood "
    "without the conversation. Use only the conversation below. Always name the client or "
    'company the conversation is about, and replace words such as "it", "their", '
    '"the first one", "and the ...?" or "¿y ...?" with what they refer to, using '
    "the exact names, places, dates and documents from the conversation. Keep the language "
    "of the last message. Do not answer it. Output only the question.\n\n"
    "Example:\nuser: What did we agree with Northwind Bakery?\nassistant: A fee of EUR 9,000.\n"
    "Last message: And when does it start?\n"
    "Standalone question: When does the agreement with Northwind Bakery start?\n\n"
    "Ejemplo:\nuser: ¿Qué tiendas tiene Ferretería Soto en el piloto?\n"
    "assistant: Centro y Delicias.\nLast message: ¿Y las ventas de la primera?\n"
    "Standalone question: ¿Cuáles son las ventas de la tienda Centro de Ferretería Soto?\n\n"
    "Conversation:\n{history}\n\nLast message: {question}\nStandalone question:"
)
LABELS = {"draft": "DRAFT (superseded)", "signed": "SIGNED (current version)"}


@dataclass(frozen=True)
class Turn:
    role: str  # "user" or "assistant"
    content: str


class ChatStore:
    """Turns kept per chat; a chat's history never includes another chat's turns."""

    def __init__(self) -> None:
        self._chats: dict[str, list[Turn]] = {}

    def add(self, chat_id: str, turn: Turn) -> None:
        self._chats.setdefault(chat_id, []).append(turn)

    def history(self, chat_id: str) -> list[Turn]:
        return list(self._chats.get(chat_id, []))


def rewrite_prompt(question: str, history: Sequence[Turn]) -> str:
    lines = [f"{t.role}: {t.content}" for t in history[-MAX_TURNS:]]
    return PROMPT.format(history="\n".join(lines), question=question)


def rewrite(question: str, history: Sequence[Turn], generate: Callable[[str], str]) -> str:
    if not history:
        return question
    answer = generate(rewrite_prompt(question, history)).strip()
    answer = answer.splitlines()[0].strip().strip('"').strip("“”").strip() if answer else ""
    return answer or question


def ollama_generate(prompt: str) -> str:
    import ollama

    from company_brain.embedding import ollama_url

    response = ollama.Client(host=ollama_url()).chat(
        model=ANSWER_MODEL,
        messages=[{"role": "user", "content": prompt}],
        think=False,
        options={"temperature": 0, "seed": 7, "num_predict": 80, "num_ctx": 4096},
        keep_alive="30m",
    )
    return str(response.message.content or "")


def conflict_labels(
    kept: Sequence[Hit], counterparts: Sequence[Hit], statuses: Mapping[str, str | None]
) -> dict[str, str]:
    """Chunk ID -> label, for chunks of agreements that exist as draft and signed."""
    hits = [*kept, *counterparts]
    present = {statuses.get(h.doc_id) for h in hits}
    if not present & {"draft", "signed"}:
        return {}
    return {
        h.chunk_id: LABELS[status] for h in hits if (status := statuses.get(h.doc_id)) in LABELS
    }
