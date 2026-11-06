"""T34.AC3: the judge runs under the firewall-blocked Python, and the firewall check
passes before and after (this confirms enforcement; it does not log every connection)."""

import json

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

CHECK = """
import json, os, sys
import ollama
import company_brain.egress as egress
import company_brain.eval_run as eval_run
python = os.path.realpath(sys.executable)
before = egress.lulu_problems({python: python})
judge = eval_run.ragas_judge("http://127.0.0.1:11434")
scores = judge([
    {"id": "a", "question": "What fee did we agree?", "answer": "The fee is EUR 36,500. [1]",
     "contexts": ["Clause 3. The client pays a fixed fee of EUR 36,500."]},
    {"id": "b", "question": "What fee did we agree?", "answer": "The fee is EUR 90,000. [1]",
     "contexts": ["Clause 3. The client pays a fixed fee of EUR 36,500."]},
])
after = egress.lulu_problems({python: python})
ollama.Client(host="http://127.0.0.1:11434").generate(model=eval_run.JUDGE_MODEL, prompt="",
                                                       keep_alive=0)
print(json.dumps({"blocked": egress.blocked(python), "before": before, "after": after,
                  "scores": scores}))
"""


def test_judge_under_the_blocked_python(run_offline) -> None:  # type: ignore[no-untyped-def]
    result = run_offline(CHECK, timeout=1800)
    assert result.returncode == 0, result.stderr[-3000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["blocked"] and out["before"] == [] and out["after"] == []
    good, bad = out["scores"]
    assert good["faithfulness"] > bad["faithfulness"], out["scores"]
