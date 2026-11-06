"""T26.AC1 with the answering model on the tuning clients' call transcripts.

At least 90% of the decisions and action items recorded in facts.json must be
found: same owner, and the normalised action or decision text contained in, or
nearly equal to, the summary item. The locked clients' calls are not used, so
nothing is tuned on them (T14).
"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

CHECK = """
import json, tempfile
from pathlib import Path
import ollama
import company_brain.corpus as corpus
import company_brain.summarise as summarise
from company_brain.content_guard import GuardedReader, Manifest
from company_brain.convert import convert

root = Path(tempfile.mkdtemp())
built = corpus.build(root / corpus.PREFIX)
reader = GuardedReader(root, Manifest.load(Path("data/manifests")))
tuning = {"Pellmore Dental Group", "Quillstone Hotels", "Conservas Arrieta", "Talleres Benamar"}
out = []
for entry in built.files:
    if entry["client"] in tuning and entry["role"] in ("call-vtt", "call-docx"):
        result = summarise.summarise(convert(reader.read(root / entry["path"])),
                                     summarise.ollama_generate)
        out.append({"path": entry["path"], "warnings": result.warnings, "text": result.text,
                    "items": [[i.kind, i.text, i.owner, i.due] for i in result.items]})
client = ollama.Client(host="http://127.0.0.1:11434")
client.generate(model=summarise.ANSWER_MODEL, prompt="", keep_alive=0)
print(json.dumps(out, ensure_ascii=False))
"""


def key(text: str) -> str:
    import re
    import unicodedata

    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def found(fact: dict, items: list[list[str]]) -> bool:  # type: ignore[type-arg]
    import difflib

    want = key(fact.get("action") or fact["decision"])
    for kind, text, owner, _due in items:
        if fact["type"] == "action" and (kind != "actions" or key(owner) != key(fact["owner"])):
            continue
        if fact["type"] == "decision" and kind != "decisions":
            continue
        got = key(text)
        if (
            want in got
            or got in want
            and len(got) > 10
            or (difflib.SequenceMatcher(None, want, got).ratio() >= 0.85)
        ):
            return True
    return False


def test_decisions_and_actions_are_found(run_offline) -> None:  # type: ignore[no-untyped-def]
    result = run_offline(CHECK, timeout=1800)
    assert result.returncode == 0, result.stderr[-2000:]
    summaries = {s["path"]: s for s in json.loads(result.stdout.strip().splitlines()[-1])}
    facts = json.loads(Path("data/facts.json").read_text(encoding="utf-8"))["facts"]
    wanted = [
        f
        for f in facts
        if f["type"] in ("decision", "action") and f.get("location", {}).get("file") in summaries
    ]
    assert len(wanted) >= 30
    misses = [f["id"] for f in wanted if not found(f, summaries[f["location"]["file"]]["items"])]
    share = 1 - len(misses) / len(wanted)
    print(f"found {share:.0%} of {len(wanted)}; misses: {misses}")
    assert share >= 0.9, misses
    assert all("[not in sources]" not in s["text"] for s in summaries.values())
