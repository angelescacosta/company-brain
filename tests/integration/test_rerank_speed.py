"""T22.AC3: reranking 40 passages takes under 5 seconds median (Apple GPU)."""

import statistics
import time

import pytest

from company_brain.rerank import Reranker

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_forty_passages_under_five_seconds() -> None:
    reranker = Reranker()
    passages = [("Clause 4. The fee is EUR 48,000, payable within 30 days. " * 25)] * 40
    reranker.scores("What fee was agreed?", passages[:2])  # warm-up
    times = []
    for _ in range(5):
        start = time.monotonic()
        reranker.scores("What fee was agreed?", passages)
        times.append(time.monotonic() - start)
    assert statistics.median(times) < 5.0, times
