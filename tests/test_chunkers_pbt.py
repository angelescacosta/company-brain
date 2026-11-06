"""T18 PBT: hard budget, transcript round trip and exactly-once coverage.

Generated transcripts (several speakers, very long turns and sentences, Spanish
text), contracts (clause counts and numbering styles) and tables (long rows),
for every budget. Properties: no chunk (label included) is over the budget;
removing overlaps rebuilds the transcript exactly, and each overlap is one
whole turn or absent and flagged; every clause and every table row appears
exactly once, whole or as flagged parts. This protects against lost or
duplicated evidence.
"""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from company_brain.chunkers import (
    BUDGETS,
    chunk_contract,
    chunk_table,
    chunk_transcript,
    count_tokens,
    reconstruct,
)
from company_brain.convert import Element

WORDS = [
    "el",
    "presupuesto",
    "se",
    "envía",
    "mañana",
    "the",
    "pilot",
    "starts",
    "in",
    "León",
    "árbol",
    "niño",
    "48,000",
    "EUR",
    "señora",
    "Teatinos",
    "approval",
    "(draft)",
    "y",
    "con",
]
sentence = st.lists(st.sampled_from(WORDS), min_size=1, max_size=60).map(lambda w: " ".join(w))
punct = st.sampled_from([".", ",", ";", ":", "?", "!", ""])
turn_text = st.lists(st.tuples(sentence, punct), min_size=1, max_size=40).map(
    lambda parts: " ".join(s + p for s, p in parts)
)
speakers = st.sampled_from(["Dana Whitcombe", "Iñaki Arrieta", "Sofía Lindqvist", "Rocío"])
budgets = st.sampled_from(BUDGETS)
fast = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@fast
@given(st.lists(st.tuples(speakers, turn_text), min_size=1, max_size=25), budgets)
def test_transcripts(turns: list[tuple[str, str]], budget: int) -> None:
    elements = [
        Element("cue", f"{s}: {t}", {"cue": i + 1, "start": f"00:00:{i:02d}.000"})
        for i, (s, t) in enumerate(turns)
    ]
    chunks = chunk_transcript(elements, budget, "Client | Call.vtt | Transcript", "Call.vtt")
    assert all(count_tokens(c.full()) <= budget for c in chunks)
    assert reconstruct(chunks) == [e.text for e in elements]
    for previous, chunk in zip(chunks, chunks[1:], strict=False):
        if chunk.overlap:
            assert chunk.overlap == 1
            first = chunk.parts[0]
            assert first.whole and first == previous.parts[-1]
        else:
            assert "no_overlap" in chunk.flags


clause_style = st.sampled_from(["{n}. ", "{n}) ", "Clause {n}. ", "Cláusula {n}. ", "{n}.1 "])


@fast
@given(
    st.lists(st.tuples(turn_text, st.integers(0, 3)), min_size=1, max_size=15),
    clause_style,
    budgets,
)
def test_contracts(clauses: list[tuple[str, int]], style: str, budget: int) -> None:
    elements = [Element("heading", "CONSULTING AGREEMENT (FICTIONAL)", {"paragraph": 0})]
    expected = []
    paragraph = 1
    for number, (text, extra) in enumerate(clauses, start=1):
        body = [style.format(n=number) + text] + [
            f"Further text {k}: {text[:80]}" for k in range(extra)
        ]
        for line in body:
            elements.append(Element("paragraph", line, {"paragraph": paragraph}))
            paragraph += 1
        expected.append("\n".join(body))
    chunks = chunk_contract(elements, budget, "Client | Agreement.docx", "Agreement.docx")
    assert all(count_tokens(c.full()) <= budget for c in chunks)
    rebuilt = reconstruct(chunks)
    assert rebuilt[0] == "CONSULTING AGREEMENT (FICTIONAL)"
    assert rebuilt[1:] == expected
    for chunk in chunks:
        if any(not p.whole for p in chunk.parts):
            assert "split" in chunk.flags


cell = st.lists(st.sampled_from(WORDS), min_size=1, max_size=120).map(lambda w: " ".join(w))


@fast
@given(
    st.integers(2, 6),
    st.lists(st.lists(cell, min_size=6, max_size=6), min_size=1, max_size=20),
    budgets,
)
def test_tables(columns: int, body: list[list[str]], budget: int) -> None:
    header = [f"Column {i}" for i in range(columns)]
    rows = [header, *[r[:columns] for r in body]]
    elements = [
        Element("table_row", " | ".join(r), {"page": 1, "table": 1, "row": i + 1})
        for i, r in enumerate(rows)
    ]
    chunks = chunk_table(elements, budget, "Client | Report.pdf | Table 1", "Report.pdf#table-1")
    assert all(count_tokens(c.full()) <= budget for c in chunks)
    assert reconstruct(chunks) == [e.text for e in elements[1:]]
