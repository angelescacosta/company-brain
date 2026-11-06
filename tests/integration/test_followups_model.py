"""T23.AC1: the answering model (Qwen3.5-4B, local Ollama) resolves 10 fixture follow-ups."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_followups import DIALOGUES  # noqa: E402


@pytest.mark.integration
def test_ten_dialogues_with_the_answering_model(run_offline) -> None:  # type: ignore[no-untyped-def]
    """T23.AC1."""
    code = f"""
import json
import company_brain.followups as followups
from company_brain.followups import Turn
results = []
for first, answer, follow, subject in {DIALOGUES!r}:
    history = [Turn("user", first), Turn("assistant", answer)]
    results.append(followups.rewrite(follow, history, followups.ollama_generate))
import ollama
client = ollama.Client(host="http://127.0.0.1:11434")
client.generate(model=followups.ANSWER_MODEL, prompt="", keep_alive=0)  # free the memory
print(json.dumps(results, ensure_ascii=False))
"""
    result = run_offline(code)
    assert result.returncode == 0, result.stderr[-2000:]
    rewrites = json.loads(result.stdout.strip().splitlines()[-1])
    misses = [
        (r, d[3]) for r, d in zip(rewrites, DIALOGUES, strict=True) if d[3].lower() not in r.lower()
    ]
    assert not misses, misses
