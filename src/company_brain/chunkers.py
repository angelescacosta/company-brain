"""Chunkers with one hard token budget (T18).

Budgets are counted with the pinned tiktoken encoding o200k_base (offline,
`config/models.toml`): the embedding models' own tokenizers are not on the Mac
(only their Ollama files), and every embedding model accepts far more than the
largest budget (D34). The label, and any repeated table header, count toward
the budget.

Every chunk keeps its pieces as `parts` (unit number, text, whole or not), so
coverage can be checked: a transcript turn, contract clause or table row is a
unit; a unit longer than the budget is split at clause punctuation, then at
character boundaries, and the chunk is flagged "split". Transcripts repeat the
previous chunk's last whole turn at the start of the next chunk when it fits;
otherwise that boundary has no overlap and is flagged "no_overlap".
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

from company_brain.convert import Converted, Element

BUDGETS = (300, 500, 800)
DEFAULT_BUDGET = 500
TIKTOKEN_CACHE = Path("~/.cache/tiktoken").expanduser()
_CLAUSE = re.compile(
    r"^\s*(?:(?:clause|cl[aá]usula|section|secci[oó]n|article|art[ií]culo)\s+)?"
    r"(\d+(?:\.\d+)*|[IVXLC]+)[.)]?\s+\S",
    re.IGNORECASE,
)
_BREAK = re.compile(r"(?<=[.;:,!?])\s+")


@cache
def _encoding() -> Any:
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(TIKTOKEN_CACHE))
    import tiktoken

    return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text, disallowed_special=()))


@dataclass(frozen=True)
class Part:
    unit: int
    text: str
    whole: bool = True


@dataclass(frozen=True)
class Chunk:
    text: str
    label: str
    parent: str
    kind: str  # transcript, clause, table, section
    spans: tuple[dict[str, Any], ...]
    parts: tuple[Part, ...]
    overlap: int = 0  # leading parts repeated from the previous chunk
    flags: frozenset[str] = field(default_factory=frozenset)

    def full(self) -> str:
        """What is embedded and counted against the budget."""
        return f"{self.label}\n{self.text}"


def _fits(label: str, texts: Sequence[str], budget: int, header: str = "") -> bool:
    body = "\n".join([header, *texts] if header else texts)
    return count_tokens(f"{label}\n{body}") <= budget


def split_long(text: str, room: int) -> list[str]:
    """Pieces of at most `room` tokens that concatenate back to `text` exactly."""
    if count_tokens(text) <= room:
        return [text]
    pieces: list[str] = []
    start = 0
    for match in _BREAK.finditer(text):
        pieces.append(text[start : match.end()])
        start = match.end()
    pieces.append(text[start:])
    out: list[str] = []
    current = ""
    for piece in pieces:
        if current and count_tokens(current + piece) > room:
            out.append(current)
            current = ""
        current += piece
    out.append(current)
    final: list[str] = []
    for piece in out:
        while count_tokens(piece) > room:
            low, high = 1, len(piece)
            while low < high:  # longest prefix that fits
                middle = (low + high + 1) // 2
                if count_tokens(piece[:middle]) <= room:
                    low = middle
                else:
                    high = middle - 1
            final.append(piece[:low])
            piece = piece[low:]
        if piece:
            final.append(piece)
    return final


def _room(label: str, budget: int, header: str = "") -> int:
    used = count_tokens(f"{label}\n{header}\n" if header else f"{label}\n")
    if used >= budget:
        raise ValueError("the label alone exceeds the budget")
    return budget - used - 1  # one token of slack for merges across the joins


def _pieces(units: Sequence[str], room: int) -> list[Part]:
    parts: list[Part] = []
    for number, text in enumerate(units):
        split = split_long(text, room)
        parts += [Part(number, piece, whole=len(split) == 1) for piece in split]
    return parts


def _chunk(
    parts: list[Part],
    label: str,
    parent: str,
    kind: str,
    provenance: Sequence[dict[str, Any]],
    overlap: int = 0,
    flags: frozenset[str] = frozenset(),
    header: str = "",
) -> Chunk:
    if any(not p.whole for p in parts):
        flags |= {"split"}
    spans: list[dict[str, Any]] = []
    for unit in dict.fromkeys(p.unit for p in parts):
        if provenance[unit] not in spans:
            spans.append(provenance[unit])
    body = "\n".join([header, *(p.text for p in parts)] if header else [p.text for p in parts])
    return Chunk(body, label, parent, kind, tuple(spans), tuple(parts), overlap, flags)


def _pack(
    parts: list[Part],
    label: str,
    budget: int,
    header: str = "",
    overlap: bool = False,
) -> list[tuple[list[Part], int, frozenset[str]]]:
    """Greedy packing; with `overlap`, a chunk opens with the last whole part before it."""
    groups: list[tuple[list[Part], int, frozenset[str]]] = []
    current: list[Part] = []
    lead = 0
    flags: frozenset[str] = frozenset()
    for part in parts:
        texts = [p.text for p in current] + [part.text]
        if current and not _fits(label, texts, budget, header):
            groups.append((current, lead, flags))
            last = current[-1]
            if overlap and last.whole and _fits(label, [last.text, part.text], budget, header):
                current, lead, flags = [last], 1, frozenset()
            else:
                current, lead = [], 0
                flags = frozenset({"no_overlap"}) if overlap else frozenset()
        current.append(part)
    if current:
        groups.append((current, lead, flags))
    return groups


def chunk_transcript(turns: Sequence[Element], budget: int, label: str, parent: str) -> list[Chunk]:
    parts = _pieces([t.text for t in turns], _room(label, budget))
    provenance = [t.provenance for t in turns]
    return [
        _chunk(group, label, parent, "transcript", provenance, lead, flags)
        for group, lead, flags in _pack(parts, label, budget, overlap=True)
    ]


def clauses_of(elements: Sequence[Element]) -> list[list[Element]]:
    """Paragraphs grouped by clause; anything before the first numbered clause is clause 0."""
    groups: list[list[Element]] = [[]]
    for element in elements:
        if element.kind != "table_row" and _CLAUSE.match(element.text):
            groups.append([element])
        else:
            groups[-1].append(element)
    return [group for group in groups if group]


def chunk_contract(
    elements: Sequence[Element], budget: int, label: str, parent: str
) -> list[Chunk]:
    groups = clauses_of(elements)
    units = ["\n".join(e.text for e in group) for group in groups]
    provenance = [group[0].provenance for group in groups]
    parts = _pieces(units, _room(label, budget))
    chunks = []
    for group, _lead, flags in _pack(parts, label, budget):
        first = group[0].unit
        chunks.append(
            _chunk(group, label, f"{parent}#clause-{first}", "clause", provenance, 0, flags)
        )
    return chunks


def _short_header(header: str, budget: int, label: str) -> str:
    if count_tokens(header) <= budget // 4:
        return header
    names = " | ".join(cell.split()[0] if cell.split() else "" for cell in header.split(" | "))
    if count_tokens(names) <= budget // 4:
        return names
    return split_long(names, budget // 4)[0]


def chunk_table(rows: Sequence[Element], budget: int, label: str, parent: str) -> list[Chunk]:
    """Whole table if it fits; otherwise rows (then cells) with the header repeated."""
    header, body = rows[0].text, list(rows[1:])
    provenance = [r.provenance for r in body]
    if not body:
        return [_chunk([Part(0, header)], label, parent, "table", [rows[0].provenance])]
    if _fits(label, [header, *(r.text for r in body)], budget):
        parts = [Part(n, r.text) for n, r in enumerate(body)]
        return [_chunk(parts, label, parent, "table", provenance, header=header)]
    header = _short_header(header, budget, label)
    room = _room(label, budget, header)
    parts = []
    for number, row in enumerate(body):
        if count_tokens(row.text) <= room:
            parts.append(Part(number, row.text))
            continue
        pieces, current = [], ""
        for cell_number, cell in enumerate(row.text.split(" | ")):
            text = cell if cell_number == 0 else " | " + cell
            if current and count_tokens(current + text) > room:
                pieces.append(current)
                current = ""
            current += text
        pieces.append(current)
        for piece in pieces:
            parts += [Part(number, bit, whole=False) for bit in split_long(piece, room)]
    return [
        _chunk(group, label, parent, "table", provenance, 0, flags, header=header)
        for group, _lead, flags in _pack(parts, label, budget, header=header)
    ]


def reconstruct(chunks: Sequence[Chunk]) -> list[str]:
    """Unit texts rebuilt from the chunks with overlaps removed (pieces joined back)."""
    units: list[str] = []
    last_unit: int | None = None
    for chunk in chunks:
        for part in chunk.parts[chunk.overlap :]:
            if part.unit == last_unit and not part.whole:
                units[-1] += part.text
            else:
                units.append(part.text)
            last_unit = part.unit
    return units


def chunk_document(converted: Converted, role: str, budget: int, source: str) -> list[Chunk]:
    """Chunks for one converted file, using the chunker its role calls for."""
    relative = converted.relative
    name = PurePosixPath(relative).name
    elements = list(converted.elements)
    if role.startswith("call"):
        turns = [e for e in elements if e.kind in {"cue", "paragraph"} and ": " in e.text]
        return chunk_transcript(turns, budget, f"{source} | {name} | Transcript", relative)
    if "contract" in role or "agreement" in role:
        return chunk_contract(elements, budget, f"{source} | {name}", relative)
    return _chunk_sections(elements, budget, source, name, relative)


def _chunk_sections(
    elements: list[Element], budget: int, source: str, name: str, relative: str
) -> list[Chunk]:
    chunks: list[Chunk] = []
    heading, section = "", 0
    block: list[Element] = []

    def flush() -> None:
        if not block:
            return
        label = f"{source} | {name}" + (f" | {heading}" if heading else "")
        units = [e.text for e in block]
        parts = _pieces(units, _room(label, budget))
        for group, _lead, flags in _pack(parts, label, budget):
            chunks.append(
                _chunk(
                    group,
                    label,
                    f"{relative}#section-{section}",
                    "section",
                    [e.provenance for e in block],
                    0,
                    flags,
                )
            )
        block.clear()

    index = 0
    while index < len(elements):
        element = elements[index]
        if element.kind == "heading":
            flush()
            heading, section = element.text[:80], section + 1
            block.append(element)
        elif element.kind == "table_row":
            flush()
            table = element.provenance.get("table")
            rows = []
            while (
                index < len(elements)
                and elements[index].kind == "table_row"
                and (elements[index].provenance.get("table") == table)
            ):
                rows.append(elements[index])
                index += 1
            label = f"{source} | {name} | Table {table or section}"
            chunks += chunk_table(rows, budget, label, f"{relative}#table-{table or section}")
            continue
        else:
            block.append(element)
        index += 1
    flush()
    return chunks
