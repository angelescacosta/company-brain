"""Preliminary runtime estimate for staged tuning (T24.AC4; T25 adds the gate's time).

Projects T35 from measured timings: stage 1 builds one index per embedding
model (3), stage 2 two more chunk budgets, stage 3 retrieval only (44 questions,
reranker on and off), stage 4 the answers on the 44 tuning questions plus their
judge calls. T35 fits when the projected machine time is at most FIT_HOURS,
which leaves the nights of 28 to 30 October for runs; otherwise the reduced plan
(stage 4 on the 20-question set) applies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FIT_HOURS = 20.0
QUESTIONS = 44
REDUCED_QUESTIONS = 20
EMBEDDING_MODELS = 3
EXTRA_BUDGETS = 2
JUDGE_CALLS_PER_ANSWER = 3  # faithfulness: claim extraction and verification


@dataclass(frozen=True)
class Timings:
    answer_s: float  # median seconds per answer (retrieval to model output)
    judge_s: float  # seconds per judge call
    retrieve_s: float
    rerank_s: float
    convert_s: float  # converting every document once
    embed_s_per_chunk: float
    chunks: int


@dataclass(frozen=True)
class Plan:
    stages: dict[str, float] = field(default_factory=dict)  # hours
    total_hours: float = 0.0
    fits: bool = True
    reduced: bool = False


def project(t: Timings) -> Plan:
    index = (t.convert_s + t.embed_s_per_chunk * t.chunks) / 3600
    answers = QUESTIONS * (t.answer_s + JUDGE_CALLS_PER_ANSWER * t.judge_s) / 3600
    stages = {
        "1 embedding models": EMBEDDING_MODELS * index,
        "2 chunk budgets": EXTRA_BUDGETS * index,
        "3 reranker on or off": 2 * QUESTIONS * (t.retrieve_s + t.rerank_s) / 3600,
        "4 answers and threshold": answers,
    }
    total = sum(stages.values())
    if total <= FIT_HOURS:
        return Plan(stages, total, fits=True, reduced=False)
    reduced = dict(stages)
    reduced["4 answers and threshold"] = answers * REDUCED_QUESTIONS / QUESTIONS
    reduced_total = sum(reduced.values())
    return Plan(stages, total, fits=reduced_total <= FIT_HOURS, reduced=True)
