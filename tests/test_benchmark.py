"""T7 unit tests: memory and swap readings, prompts, medians and the CSV."""

import csv
import time
from pathlib import Path

from company_brain.benchmark import (
    CRITICAL,
    FIELDS,
    NORMAL,
    WARNING,
    BenchmarkRow,
    benchmark_model,
    median_of_medians,
    memory_used,
    pressure_level,
    prompts,
    swap_used,
    write_csv,
    write_roster,
)

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                     5439.
Pages wired down:                             100.
Pages purgeable:                                10.
File-backed pages:                            294442.
Anonymous pages:                              1010.
Pages occupied by compressor:                   50.
"""


def test_memory_used_is_app_plus_wired_plus_compressed() -> None:
    assert memory_used(VM_STAT) == (1010 - 10 + 100 + 50) * 16384


def test_swap_used() -> None:
    text = "vm.swapusage: total = 8192.00M  used = 6265.38M  free = 1926.62M  (encrypted)"
    assert swap_used(text) == round(6265.38 * 1024 * 1024)
    assert swap_used("vm.swapusage: total = 0.00M  used = 0.00M  free = 0.00M") == 0


def test_prompts_are_fixed_long_and_bilingual() -> None:
    first, second = prompts(), prompts()
    assert first == second
    assert len(first) == 10
    assert {p.language for p in first} == {"en", "es"}
    for prompt in first:
        words = len(prompt.text.split())
        assert 1800 <= words <= 2600, words  # about 3,000 tokens


def test_median_of_medians() -> None:
    assert median_of_medians([[1.0, 2.0, 9.0], [3.0, 4.0, 5.0], [7.0, 8.0, 6.0]]) == 4.0


def test_csv_and_roster_files(tmp_path: Path) -> None:
    table = [
        BenchmarkRow(
            "qwen3.5:4b",
            9_000_000_000,
            15_500_000_000,
            4_000_000_000,
            1.0,
            10.0,
            peak_pressure=WARNING,
            pressure_samples=900,
            longest_gap_s=0.7,
            pressure_verdict="pass",
        ),
    ]
    write_csv(tmp_path / "b.csv", table)
    with (tmp_path / "b.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == FIELDS
    assert rows[0]["flagged"] == "no"
    assert (rows[0]["peak_pressure"], rows[0]["pressure_verdict"]) == ("warning", "pass")
    write_roster(tmp_path / "roster.toml", table)
    text = (tmp_path / "roster.toml").read_text()
    assert 'models = ["qwen3.5:4b"]' in text
    assert 'path = "single-model"' in text


def test_pressure_level_reading() -> None:
    assert [pressure_level(s) for s in ("1\n", "2", "4\n")] == [NORMAL, WARNING, CRITICAL]
    assert pressure_level("") is None
    assert pressure_level("sysctl: unknown oid") is None


def test_every_request_is_new(monkeypatch) -> None:
    import company_brain.benchmark as benchmark

    monkeypatch.setattr(benchmark, "read_memory", lambda: 5)
    monkeypatch.setattr(benchmark, "read_swap", lambda: 0)
    monkeypatch.setattr(benchmark, "read_pressure", lambda: NORMAL)
    sent: list[str] = []

    def generate(_model: str, text: str) -> tuple[float, float]:
        sent.append(text)
        return 1.0, 2.0

    row = benchmark_model("m", generate, prompts()[:2], runs=3, idle_memory=4)
    assert len(sent) == 1 + 2 * 3
    assert len(set(sent)) == len(sent)
    assert (row.first_token_s, row.total_s, row.idle_memory) == (1.0, 2.0, 4)
    assert row.pressure_samples >= 1 and row.pressure_verdict == "pass"


def test_a_critical_spike_fails_the_run(monkeypatch) -> None:
    import company_brain.benchmark as benchmark

    readings = iter([NORMAL, CRITICAL])
    monkeypatch.setattr(benchmark, "read_memory", lambda: 5)
    monkeypatch.setattr(benchmark, "read_swap", lambda: 0)
    monkeypatch.setattr(benchmark, "read_pressure", lambda: next(readings, NORMAL))

    def generate(_model: str, _text: str) -> tuple[float, float]:
        time.sleep(0.3)
        return 1.0, 2.0

    row = benchmark_model("m", generate, prompts()[:1], runs=1, interval=0.05)
    assert row.peak_pressure == CRITICAL and row.pressure_verdict == "fail"


def test_settle_waits_for_steady_memory(monkeypatch) -> None:
    import company_brain.benchmark as benchmark

    readings = iter([10_000_000_000, 9_000_000_000, 8_500_000_000, 8_450_000_000])
    monkeypatch.setattr(benchmark.time, "sleep", lambda _s: None)
    assert benchmark.settle(lambda: next(readings)) == 8_450_000_000
