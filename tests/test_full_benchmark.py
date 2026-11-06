"""T33 unit tests: the long transcript, one timed run, the targets and the CSV."""

import csv
import time
import tomllib
from pathlib import Path

from company_brain.benchmark import (
    CRITICAL,
    FULL_FIELDS,
    NORMAL,
    TARGETS_S,
    BenchmarkRow,
    FullRun,
    full_verdict,
    long_transcript,
    measure_run,
    write_full_csv,
    write_full_roster,
)
from company_brain.convert import vtt_elements

REPO = Path(__file__).resolve().parents[1]


def test_long_transcript_is_a_one_hour_call_of_about_9000_words() -> None:
    data = long_transcript(seed=1)
    cues = vtt_elements(data)
    words = sum(len(cue.text.split()) for cue in cues)
    assert 8800 <= words <= 9300
    assert cues[-1].provenance["end"] >= "00:59:00.000"
    assert all("speaker" in cue.provenance for cue in cues)
    assert long_transcript(seed=1) == data  # fixed input
    assert long_transcript(seed=2) != data  # each warm run is a new call, so no cache helps
    assert vtt_elements(long_transcript(seed=3, language="es"))


def _quiet(monkeypatch, pressure=lambda: NORMAL) -> None:
    import company_brain.benchmark as benchmark

    monkeypatch.setattr(benchmark, "read_memory", lambda: 5)
    monkeypatch.setattr(benchmark, "read_swap", lambda: 0)
    monkeypatch.setattr(benchmark, "read_pressure", pressure)


def test_a_run_is_timed_until_the_checked_answer_returns(monkeypatch) -> None:
    _quiet(monkeypatch)
    run = measure_run("m", "answer", 2, False, lambda: time.sleep(0.2), idle_memory=4)
    assert (run.mode, run.run, run.cold) == ("answer", 2, False)
    assert run.row.total_s >= 0.2
    assert run.row.first_token_s == run.row.total_s  # the checked answer appears whole
    assert run.row.pressure_verdict == "pass" and run.row.idle_memory == 4


def test_a_critical_spike_fails_its_run(monkeypatch) -> None:
    readings = iter([NORMAL, CRITICAL])
    _quiet(monkeypatch, lambda: next(readings, NORMAL))
    run = measure_run("m", "draft", 0, True, lambda: time.sleep(0.3), interval=0.05)
    assert run.row.peak_pressure == CRITICAL and run.row.pressure_verdict == "fail"


def _run(mode: str, number: int, seconds: float, cold: bool = False, verdict: str = "pass"):
    row = BenchmarkRow("m", 1, 2, 0, seconds, seconds, pressure_verdict=verdict)
    return FullRun(row, mode, number, cold)


def _complete(seconds: dict[str, float]) -> list[FullRun]:
    runs = []
    for mode, value in seconds.items():
        runs.append(_run(mode, 0, value * 3, cold=True))
        runs += [_run(mode, n, value) for n in (1, 2, 3)]
    return runs


def test_targets_use_the_warm_median_and_every_run_pressure() -> None:
    fast = {mode: target - 1 for mode, target in TARGETS_S.items()}
    assert full_verdict(_complete(fast))["result"] == "pass"
    # A slow cold start is recorded but is not a time target.
    assert full_verdict(_complete(fast))["modes"]["answer"]["cold_s"] == fast["answer"] * 3
    slow = {**fast, "summarise": TARGETS_S["summarise"] + 1}
    verdict = full_verdict(_complete(slow))
    assert verdict["result"] == "fail" and not verdict["modes"]["summarise"]["met"]
    # One warm run over the target does not move the median past it.
    runs = _complete(fast)
    runs[1] = _run("answer", 1, 500)
    assert full_verdict(runs)["result"] == "pass"
    # Critical pressure in the cold run fails, an unreadable trace is not a pass.
    runs = _complete(fast)
    runs[0] = _run("answer", 0, 1, cold=True, verdict="fail")
    assert full_verdict(runs)["result"] == "fail"
    runs = _complete(fast)
    runs[5] = _run("summarise", 1, 1, verdict="inconclusive")
    assert full_verdict(runs)["result"] == "inconclusive"
    # A mode without its three warm runs and one cold run is not measured.
    assert full_verdict(_complete(fast)[1:])["result"] == "inconclusive"


def test_csv_follows_the_dashboard_contract(tmp_path: Path) -> None:
    contract = tomllib.loads((REPO / "config" / "dashboard_inputs.toml").read_text())
    spec = contract["benchmark_full"]
    assert set(spec["fields"]) <= set(FULL_FIELDS) <= set(spec["fields"]) | set(spec["optional"])
    path = tmp_path / "benchmark_full.csv"
    write_full_csv(path, _complete({"answer": 10.0}))
    with path.open() as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == FULL_FIELDS
    assert [(r["mode"], r["run"], r["cold"]) for r in rows[:2]] == [
        ("answer", "0", "yes"),
        ("answer", "1", "no"),
    ]


def test_roster_records_the_full_benchmark(tmp_path: Path) -> None:
    path = tmp_path / "roster.toml"
    path.write_text('[roster]\nmodels = ["m"]\npath = "single-model"\n', encoding="utf-8")
    fast = {mode: 1.0 for mode in TARGETS_S}
    write_full_roster(path, full_verdict(_complete(fast)))
    first = tomllib.loads(path.read_text())
    assert first["roster"]["models"] == ["m"]
    assert first["full_benchmark"]["result"] == "pass"
    slow = {**fast, "draft": TARGETS_S["draft"] + 5}
    write_full_roster(path, full_verdict(_complete(slow)))
    second = tomllib.loads(path.read_text())
    assert second["full_benchmark"]["result"] == "fail"
    assert second["full_benchmark"]["missed"] == ["draft"]
    assert second["roster"] == first["roster"]
