"""Early model benchmark and candidate roster (T7).

Memory is "Memory Used" as Activity Monitor defines it: app memory + wired +
compressed, read from `vm_stat` (app memory = anonymous pages minus purgeable
pages). Swap comes from `sysctl vm.swapusage`. Both are sampled for the whole
machine twice a second, so the owner's other open apps count too; the memory
before the answering model loads is recorded as the baseline.

Since revision 9 (H12, D32) memory and swap are recorded but are not limits:
a run passes on macOS memory pressure (`kern.memorystatus_vm_pressure_level`),
sampled with a timestamp at the same rate. Any critical reading fails the run;
a missing reading or a gap longer than MAX_GAP_S makes it inconclusive.
"""

from __future__ import annotations

import csv
import random
import re
import statistics
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ANSWER_MODEL = "qwen3.5:4b"  # the one answering model since revision 9
EMBEDDING_MODEL = "bge-m3:latest"  # the largest embedding option, so the worst case
NORMAL, WARNING, CRITICAL = 1, 2, 4  # kern.memorystatus_vm_pressure_level values
PRESSURE_NAMES = {NORMAL: "normal", WARNING: "warning", CRITICAL: "critical"}
MAX_GAP_S = 2.0
RUNS = 3
OPTIONS = {"num_ctx": 16384, "num_predict": 400, "temperature": 0, "seed": 7}
FIELDS = [
    "model",
    "baseline_memory_bytes",
    "peak_memory_bytes",
    "swap_growth_bytes",
    "first_token_s",
    "total_s",
    "flagged",
    "idle_memory_bytes",
    "peak_pressure",
    "pressure_samples",
    "longest_gap_s",
    "pressure_verdict",
]


@dataclass(frozen=True)
class BenchmarkRow:
    model: str
    baseline_memory: int
    peak_memory: int
    swap_growth: int
    first_token_s: float
    total_s: float
    # The Mac before any AI model is loaded, for reading the AI's own share.
    idle_memory: int = 0
    peak_pressure: int = 0
    pressure_samples: int = 0
    longest_gap_s: float = 0.0
    pressure_verdict: str = "inconclusive"


def pressure_verdict(trace: Sequence[tuple[float, int | None]]) -> str:
    """ "fail" on any critical reading, "inconclusive" on a gap or missing reading, else "pass"."""
    readings = [level for _time, level in trace]
    if CRITICAL in readings:
        return "fail"
    times = [moment for moment, _level in trace]
    gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    if not trace or None in readings or any(gap > MAX_GAP_S for gap in gaps):
        return "inconclusive"
    return "pass"


def flagged(row: BenchmarkRow) -> bool:
    return row.pressure_verdict != "pass"


def roster(table: Iterable[BenchmarkRow]) -> list[str]:
    """The answering model when its run passed, otherwise nothing (blocked)."""
    return [ANSWER_MODEL] if any(r.model == ANSWER_MODEL and not flagged(r) for r in table) else []


def roster_path(chosen: Sequence[str]) -> str:
    return {0: "blocked", 1: "single-model"}.get(len(chosen), "two models")


def _field(text: str, name: str) -> int:
    match = re.search(rf"^{re.escape(name)}:\s+(\d+)\.", text, re.MULTILINE)
    return int(match[1]) if match else 0


def memory_used(vm_stat: str) -> int:
    page = int(re.search(r"page size of (\d+) bytes", vm_stat)[1])  # type: ignore[index]
    app = _field(vm_stat, "Anonymous pages") - _field(vm_stat, "Pages purgeable")
    wired = _field(vm_stat, "Pages wired down")
    compressed = _field(vm_stat, "Pages occupied by compressor")
    return (app + wired + compressed) * page


def swap_used(swapusage: str) -> int:
    match = re.search(r"used = ([\d.]+)M", swapusage)
    return round(float(match[1]) * 1024 * 1024) if match else 0


def read_memory() -> int:
    return memory_used(subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True).stdout)


def pressure_level(output: str) -> int | None:
    text = output.strip()
    return int(text) if text in {"1", "2", "4"} else None


def read_pressure() -> int | None:
    output = subprocess.run(
        ["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
        capture_output=True,
        text=True,
    ).stdout
    return pressure_level(output)


def read_swap() -> int:
    output = subprocess.run(
        ["/usr/sbin/sysctl", "vm.swapusage"], capture_output=True, text=True
    ).stdout
    return swap_used(output)


class Sampler:
    """Peak memory used, peak swap and a timestamped pressure trace while a model runs."""

    def __init__(self, interval: float = 0.5) -> None:
        self.interval = interval
        self.peak_memory = 0
        self.peak_swap = 0
        self.trace: list[tuple[float, int | None]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak_memory = max(self.peak_memory, read_memory())
            self.peak_swap = max(self.peak_swap, read_swap())
            self.trace.append((time.monotonic(), read_pressure()))
            self._stop.wait(self.interval)

    def __enter__(self) -> Sampler:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()


@dataclass(frozen=True)
class Prompt:
    language: str
    text: str


_EN = {
    "firms": [
        "Northwind Logistics",
        "Bluepeak Retail",
        "Corvid Health",
        "Lumen Foods",
        "Atlas Mobility",
    ],
    "topics": [
        "supplier onboarding",
        "warehouse staffing",
        "pricing reviews",
        "customer churn",
        "IT ticket backlogs",
        "quarterly forecasting",
        "training budgets",
    ],
    "sentences": [
        "In week {n}, the {topic} team at {firm} reported {k} open issues and {m} closed ones.",
        "The steering group of {firm} asked for a clearer owner for {topic} before month {n}.",
        "Interviews with {k} managers showed that {topic} depends on spreadsheets updated by hand.",
        "A pilot in region {n} cut the time spent on {topic} by {k} percent over {m} weeks.",
        "The team at {firm} estimated that {topic} costs about {k} thousand euros per quarter.",
        "Three risks were logged for {topic}: missing data, unclear approvals, {m} late handovers.",
        "The consultant recommended a weekly review of {topic} with {k} named decision makers.",
    ],
    "question": "Using only the notes above, write a structured summary for the managing "
    "partner: the three main problems, the evidence for each, and five recommended next steps.",
}
_ES = {
    "firms": [
        "Logística Norte",
        "Comercial Brisa",
        "Salud Corvino",
        "Alimentos Lumen",
        "Movilidad Atlas",
    ],
    "topics": [
        "alta de proveedores",
        "plantilla del almacén",
        "revisión de precios",
        "pérdida de clientes",
        "incidencias de TI",
        "previsión trimestral",
        "formación",
    ],
    "sentences": [
        "En la semana {n}, {topic} en {firm} tenía {k} incidencias abiertas y {m} cerradas.",
        "El comité de {firm} pidió un responsable claro para {topic} antes del mes {n}.",
        "Las entrevistas con {k} responsables mostraron que {topic} depende de hojas manuales.",
        "Un piloto en la región {n} redujo el tiempo de {topic} un {k} por ciento en {m} semanas.",
        "El equipo de {firm} calculó que {topic} cuesta unos {k} mil euros por trimestre.",
        "Hubo tres riesgos en {topic}: datos incompletos, aprobaciones confusas y {m} retrasos.",
        "La consultora recomendó una revisión semanal de {topic} con {k} personas que decidan.",
    ],
    "question": "Usando solo las notas anteriores, escribe un resumen estructurado para la socia "
    "directora: los tres problemas principales, la evidencia de cada uno y cinco próximos pasos.",
}


def prompts(count: int = 10, words: int = 2100) -> list[Prompt]:
    """Ten fixed prompts of about 3,000 tokens each, alternating English and Spanish."""
    found = []
    for index in range(count):
        language, bank = ("en", _EN) if index % 2 == 0 else ("es", _ES)
        rng = random.Random(1000 + index)  # noqa: S311 - fixed text, not security
        lines: list[str] = []
        while sum(len(line.split()) for line in lines) < words:
            lines.append(
                rng.choice(bank["sentences"]).format(
                    firm=rng.choice(bank["firms"]),
                    topic=rng.choice(bank["topics"]),
                    n=rng.randint(1, 12),
                    k=rng.randint(2, 40),
                    m=rng.randint(1, 9),
                )
            )
        found.append(Prompt(language, "\n".join(lines) + "\n\n" + str(bank["question"])))
    return found


def median_of_medians(values: Sequence[Sequence[float]]) -> float:
    return statistics.median(statistics.median(runs) for runs in values)


Generate = Callable[[str, str], tuple[float, float]]


def settle(
    read: Callable[[], int] = read_memory,
    quiet: int = 200_000_000,
    window: float = 10.0,
    limit: float = 180.0,
) -> int:
    """Wait until memory used changes by less than `quiet` bytes over `window` seconds."""
    deadline = time.monotonic() + limit
    previous = read()
    while time.monotonic() < deadline:
        time.sleep(window)
        current = read()
        if abs(current - previous) < quiet:
            return current
        previous = current
    return previous


def benchmark_model(
    model: str,
    generate: Generate,
    items: Sequence[Prompt],
    runs: int = RUNS,
    idle_memory: int = 0,
    interval: float = 0.5,
) -> BenchmarkRow:
    """One warm-up answer, then `runs` warm answers per prompt; medians per prompt, then overall.

    Every request starts with its own number, so no answer reuses Ollama's cache
    of an earlier identical prompt (real questions are always new).
    """
    baseline, swap_before = read_memory(), read_swap()
    count = 0

    def fresh(text: str) -> str:
        nonlocal count
        count += 1
        return f"Request {count}.\n\n{text}"

    with Sampler(interval) as sampler:
        generate(model, fresh(items[0].text))
        first: list[list[float]] = []
        total: list[list[float]] = []
        for item in items:
            timings = [generate(model, fresh(item.text)) for _ in range(runs)]
            first.append([t[0] for t in timings])
            total.append([t[1] for t in timings])
    times = [moment for moment, _level in sampler.trace]
    gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    return BenchmarkRow(
        model=model,
        baseline_memory=baseline,
        peak_memory=max(sampler.peak_memory, baseline),
        swap_growth=max(0, sampler.peak_swap - swap_before),
        first_token_s=round(median_of_medians(first), 2),
        total_s=round(median_of_medians(total), 2),
        idle_memory=idle_memory,
        peak_pressure=max((level or 0 for _t, level in sampler.trace), default=0),
        pressure_samples=len(sampler.trace),
        longest_gap_s=round(max(gaps, default=0.0), 2),
        pressure_verdict=pressure_verdict(sampler.trace),
    )


def write_csv(path: Path, table: Sequence[BenchmarkRow]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        for row in table:
            writer.writerow(
                [
                    row.model,
                    row.baseline_memory,
                    row.peak_memory,
                    row.swap_growth,
                    row.first_token_s,
                    row.total_s,
                    "yes" if flagged(row) else "no",
                    row.idle_memory,
                    PRESSURE_NAMES.get(row.peak_pressure, "unknown"),
                    row.pressure_samples,
                    row.longest_gap_s,
                    row.pressure_verdict,
                ]
            )
    from company_brain import dashboard

    dashboard.rebuild_after("benchmark")


def write_roster(path: Path, table: Sequence[BenchmarkRow]) -> None:
    chosen = roster(table)
    models = ", ".join(f'"{m}"' for m in chosen)
    path.write_text(
        "# Answering model (T7, revision 9: one model, memory pressure rule).\n"
        "[roster]\n"
        f"models = [{models}]\n"
        f'path = "{roster_path(chosen)}"\n'
        f'source = "reports/benchmark_early.csv"\n'
        f'measured = "{time.strftime("%Y-%m-%d")}"\n',
        encoding="utf-8",
    )


# Full-pipeline benchmark (T33) ---------------------------------------------------------

TARGETS_S = {"answer": 60.0, "summarise": 300.0, "draft": 300.0}  # revision 9 targets
WARM_RUNS = 3
FULL_FIELDS = [*FIELDS, "mode", "run", "cold"]
_SPEAKERS = {"en": ("Alex Morgan", "Jordan Reyes"), "es": ("Lucía Ferrer", "Martín Ochoa")}


@dataclass(frozen=True)
class FullRun:
    row: BenchmarkRow
    mode: str
    run: int  # 0 is the cold run, 1 to 3 the warm runs
    cold: bool


def _clock(seconds: float) -> str:
    whole = int(seconds)
    millis = round((seconds - whole) * 1000)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}.{millis:03d}"


def long_transcript(
    seed: int, language: str = "en", words: int = 9000, seconds: float = 3600.0
) -> bytes:
    """A fictional one-hour call as WebVTT, about `words` words with speakers on every cue.

    Built from the same fixed sentence banks as the T7 prompts; a new seed gives a
    new call, so no warm run is answered from an earlier one's cache.
    """
    bank = _EN if language == "en" else _ES
    speakers = _SPEAKERS[language]
    rng = random.Random(5000 + seed)  # noqa: S311 - fixed text, not security
    cues: list[str] = []
    total = 0
    while total < words:
        speaker = speakers[len(cues) % 2]
        text = " ".join(
            rng.choice(bank["sentences"]).format(
                firm=rng.choice(bank["firms"]),
                topic=rng.choice(bank["topics"]),
                n=rng.randint(1, 12),
                k=rng.randint(2, 40),
                m=rng.randint(1, 9),
            )
            for _ in range(rng.randint(1, 2))
        )
        cues.append(f"<v {speaker}>{text}")
        total += len(f"{speaker}: {text}".split())
    blocks = ["WEBVTT", "NOTE Fictional call made for the T33 benchmark."]
    start = 0.0
    for cue in cues:
        end = start + seconds * len(cue.split()) / total
        blocks.append(f"{_clock(start)} --> {_clock(end)}\n{cue}")
        start = end
    return ("\n\n".join(blocks) + "\n").encode()


def measure_run(
    model: str,
    mode: str,
    run: int,
    cold: bool,
    call: Callable[[], object],
    idle_memory: int = 0,
    interval: float = 0.5,
) -> FullRun:
    """Time one request until its checked answer returns, sampling memory and pressure."""
    baseline, swap_before = read_memory(), read_swap()
    with Sampler(interval) as sampler:
        began = time.monotonic()
        call()
        total = round(time.monotonic() - began, 2)
    times = [moment for moment, _level in sampler.trace]
    gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    row = BenchmarkRow(
        model=model,
        baseline_memory=baseline,
        peak_memory=max(sampler.peak_memory, baseline),
        swap_growth=max(0, sampler.peak_swap - swap_before),
        first_token_s=total,  # the Pipe shows the checked answer whole, not streamed
        total_s=total,
        idle_memory=idle_memory,
        peak_pressure=max((level or 0 for _t, level in sampler.trace), default=0),
        pressure_samples=len(sampler.trace),
        longest_gap_s=round(max(gaps, default=0.0), 2),
        pressure_verdict=pressure_verdict(sampler.trace),
    )
    return FullRun(row, mode, run, cold)


def full_verdict(runs: Sequence[FullRun]) -> dict[str, Any]:
    """Per mode: warm median against its target and pressure in every run, warm and cold.

    "fail" when a measured mode misses its time or reads critical pressure;
    "inconclusive" when nothing failed but a run is missing or a trace is unreadable.
    """
    modes: dict[str, dict[str, Any]] = {}
    for mode, target in TARGETS_S.items():
        mine = [r for r in runs if r.mode == mode]
        warm = [r.row.total_s for r in mine if not r.cold]
        cold = [r.row.total_s for r in mine if r.cold]
        verdicts = {r.row.pressure_verdict for r in mine}
        complete = len(warm) == WARM_RUNS and len(cold) == 1
        median = statistics.median(warm) if warm else None
        pressure = "pass" if verdicts == {"pass"} else "inconclusive"
        pressure = "fail" if "fail" in verdicts else pressure
        timed = median is not None and median <= target
        modes[mode] = {
            "target_s": target,
            "warm_median_s": median,
            "cold_s": cold[0] if cold else None,
            "pressure": pressure,
            "complete": complete,
            "met": complete and timed and pressure == "pass",
            "failed": pressure == "fail" or (complete and not timed),
        }
    if any(m["failed"] for m in modes.values()):
        result = "fail"
    elif all(m["met"] for m in modes.values()):
        result = "pass"
    else:
        result = "inconclusive"
    return {"result": result, "modes": modes}


def write_full_csv(path: Path, runs: Sequence[FullRun]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(FULL_FIELDS)
        for run in runs:
            row = run.row
            writer.writerow(
                [
                    row.model,
                    row.baseline_memory,
                    row.peak_memory,
                    row.swap_growth,
                    row.first_token_s,
                    row.total_s,
                    "yes" if flagged(row) else "no",
                    row.idle_memory,
                    PRESSURE_NAMES.get(row.peak_pressure, "unknown"),
                    row.pressure_samples,
                    row.longest_gap_s,
                    row.pressure_verdict,
                    run.mode,
                    run.run,
                    "yes" if run.cold else "no",
                ]
            )
    from company_brain import dashboard

    dashboard.rebuild_after("benchmark")


def write_full_roster(path: Path, verdict: dict[str, Any]) -> None:
    """Record the T33 result next to the roster, replacing an earlier one (T33.AC3)."""
    text = path.read_text(encoding="utf-8").split("\n[full_benchmark]")[0].rstrip("\n")
    missed = ", ".join(f'"{mode}"' for mode, m in verdict["modes"].items() if not m["met"])
    path.write_text(
        f"{text}\n\n[full_benchmark]\n"
        f'result = "{verdict["result"]}"\n'
        f"missed = [{missed}]\n"
        'source = "reports/benchmark_full.csv"\n'
        f'measured = "{time.strftime("%Y-%m-%d")}"\n',
        encoding="utf-8",
    )
