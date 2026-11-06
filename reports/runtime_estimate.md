# Preliminary runtime estimate (T24.AC4)

Measured 2026-10-24 12:07 with Qwen3.5-4B, bge-m3 and the mMiniLMv2 reranker on the active index, including the output gate and its claim scores (T25).

| Measure | Seconds |
| --- | --- |
| Answer (median), search to checked answer | 13.0 |
| Output gate with claim scores (median) | 0.34 |
| Search (median) | 0.09 |
| Reranking (median) | 0.35 |
| One judge call | 5.6 |
| Converting every document once | 631 |
| Chunks in the index | 1856 |

| T35 stage | Hours |
| --- | --- |
| 1 embedding models | 0.57 |
| 2 chunk budgets | 0.38 |
| 3 reranker on or off | 0.01 |
| 4 answers and threshold | 0.36 |
| Total | 1.32 |

Fits by 31 October: yes (limit 20 hours of machine time, so the runs fit in the nights of 28 to 30 October).

Recovery checkpoint (T24.AC5): Qwen3.5-4B meets T33's targets on these questions: yes (slowest answer 15.0 s against 60 s; memory pressure pass, highest level warning, 119 samples). Measured with the owner's usual apps open; Chrome was open.

Per question:

| Question | Search | Rerank | Model | Gate | Total | Facts | Citation problems | Warnings |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| q-en-12 | 0.12 | 0.58 | 11.6 | 0.70 | 13.0 | 1 | 0 | 0 |
| q-en-13 | 0.10 | 0.35 | 6.6 | 0.10 | 7.2 | 1 | 0 | 0 |
| q-en-14 | 0.09 | 0.26 | 10.7 | 0.15 | 11.2 | 2 | 0 | 0 |
| q-es-01 | 0.08 | 0.30 | 14.3 | 0.34 | 15.0 | 2 | 0 | 2 |
| q-es-02 | 0.09 | 0.42 | 12.7 | 0.63 | 13.8 | 1 | 0 | 0 |
