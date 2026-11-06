"""Run the full-pipeline benchmark (T33) against the running assistant.

Answer and Draft go through the assistant's own service, as the chat window sends
them, and are timed until the checked answer comes back. Summarise runs the same
conversion, summary, output gate and Word and PDF export in this process on a
fictional one-hour call (about 9,000 words, made by `benchmark.long_transcript`),
because the test documents hold no call that long (D38). Per mode: one cold run
(every Ollama model unloaded first), then three warm runs on new inputs. Close
Chrome first. Writes reports/benchmark_full.csv and the [full_benchmark] section
of config/roster.toml. Takes about an hour.
"""

import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import ollama  # noqa: E402

from company_brain import export, summarise  # noqa: E402
from company_brain.backend import token_path  # noqa: E402
from company_brain.benchmark import (  # noqa: E402
    ANSWER_MODEL,
    WARM_RUNS,
    FullRun,
    full_verdict,
    long_transcript,
    measure_run,
    settle,
    write_full_csv,
    write_full_roster,
)
from company_brain.convert import Converted, vtt_elements  # noqa: E402
from company_brain.settings import Settings  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
# One cold and three warm requests per mode, each to a different client, so no
# answer is served from the cache of an earlier one.
ANSWER = [
    ("Pellmore Dental Group", "What fee did we agree with Pellmore Dental Group?"),
    ("Harrowgate Freight", "What fee did we agree with Harrowgate Freight?"),
    ("Quillstone Hotels", "What fee did we agree with Quillstone Hotels?"),
    ("Conservas Arrieta", "¿Qué honorarios acordamos con Conservas Arrieta?"),
]
DRAFT = [
    ("Pellmore Dental Group", "Draft a proposal for Pellmore Dental Group."),
    ("Harrowgate Freight", "Draft a proposal for Harrowgate Freight."),
    ("Quillstone Hotels", "Draft a proposal for Quillstone Hotels."),
    ("Talleres Benamar", "Redacta una propuesta para Talleres Benamar."),
]
SUMMARISE = [(1, "en"), (2, "en"), (3, "en"), (4, "es")]


class Service:
    """The assistant's own service, called the way the chat window calls it."""

    def __init__(self, settings: Settings) -> None:
        self.base = f"http://127.0.0.1:{settings.backend_port}"
        self.token = token_path(settings.data_dir).read_text().strip()
        self.scopes = {c["name"]: c["scope"] for c in self.post("/chat/state", {})["clients"]}

    def post(self, path: str, payload: dict[str, object]) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 - fixed loopback address
            self.base + path,
            data=json.dumps({"chat_id": "", **payload}).encode(),
            headers={"X-Company-Brain-Token": self.token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=900) as response:  # noqa: S310
            return dict(json.loads(response.read()))

    def ask(self, mode: str, client: str, message: str) -> Callable[[], dict[str, Any]]:
        scope = self.scopes[f"{client} (fictional)"]
        chat = f"t33-{time.strftime('%Y%m%d-%H%M%S')}-{mode}-{scope}"
        self.post("/chats", {"chat_id": chat, "client": scope})
        payload = {"chat_id": chat, "client": scope, "mode": mode, "message": message}
        return lambda: self.post("/ask", {**payload, "history": []})


def summary_of(seed: int, language: str, folder: Path) -> Callable[[], object]:
    data = long_transcript(seed, language)
    converted = Converted(
        f"Benchmark/Call T33-{seed}.vtt",
        hashlib.sha256(data).hexdigest(),
        "converted",
        elements=tuple(vtt_elements(data)),
    )

    def run() -> object:
        result = summarise.summarise(converted, summarise.ollama_generate)
        assert result.gated is not None  # noqa: S101 - a summary always passes the gate
        return export.export(result.gated, f"Summary T33-{seed}", folder, check=False)

    return run


def unload_all(client: ollama.Client) -> None:
    for running in client.ps().models:
        client.generate(model=str(running.model), prompt="", keep_alive=0)


def main() -> int:
    settings = Settings.from_env()
    client = ollama.Client(host=f"127.0.0.1:{settings.ollama_port}")
    service = Service(settings)
    unload_all(client)
    idle = settle()
    print(f"idle memory used {idle / 1e9:.1f} GB", flush=True)
    runs: list[FullRun] = []
    with tempfile.TemporaryDirectory() as folder:
        calls: dict[str, list[Callable[[], object]]] = {
            "answer": [service.ask("answer", c, m) for c, m in ANSWER],
            "summarise": [summary_of(s, lang, Path(folder)) for s, lang in SUMMARISE],
            "draft": [service.ask("draft", c, m) for c, m in DRAFT],
        }
        for mode, requests in calls.items():
            unload_all(client)
            settle()
            for number, call in enumerate(requests[: WARM_RUNS + 1]):
                print(f"{time.strftime('%H:%M')} {mode} run {number}", flush=True)
                run = measure_run(ANSWER_MODEL, mode, number, number == 0, call, idle_memory=idle)
                print(f"  {run.row.total_s} s, pressure {run.row.pressure_verdict}", flush=True)
                runs.append(run)
    write_full_csv(REPO / "reports" / "benchmark_full.csv", runs)
    verdict = full_verdict(runs)
    write_full_roster(REPO / "config" / "roster.toml", verdict)
    print(json.dumps(verdict, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
