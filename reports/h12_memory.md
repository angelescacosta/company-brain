# H12: memory on the owner's MacBook Air (16 GB)

Decision: D32 (one answering model, Qwen3.5-4B; pass on macOS memory pressure and the owner's verdict).

## Runs under the old limits (14 GB memory used, 1 GB swap growth)

All recorded in PRD T7 and H12 evidence: every model flagged with Chrome open, with Chrome closed, with the smaller reranker (D31) and with the helpers loaded only while searching.

## Owner's hands-on test, 2026-10-21 (Open WebUI, Qwen3.5-4B, direct connection for the test only)

| Condition | Trace | Pressure | Memory used, max | Swap growth | Owner |
| --- | --- | --- | --- | --- | --- |
| Chrome closed, 19 min | `h12/handson_chrome_closed.csv` | normal throughout | 14.0 GB | none | "It is a bit slow but I see no other major issues" |
| Chrome open, 14 min | `h12/handson_chrome_open.csv` | warning in about two thirds of samples, never critical | 15.8 GB | 4.5 GB | "It is quite slow but I can still have chrome open" |

## T7 rerun under revision 9, 2026-10-22

Configuration: Qwen3.5-4B (Ollama digest pinned in `config/models.toml`), context 16,384 tokens, bge-m3 and cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 resident, Chrome quit, 10 prompts × 3 warm runs plus one warm-up. Memory used before any model: 10.3 GB (the owner's Mail, a document and Finder open for the exercise).

| Result | Value |
| --- | --- |
| Pressure verdict | pass (highest level: warning; 1,479 samples; longest gap 0.6 s) |
| Memory used, peak | 15.3 GB (recorded, not a limit) |
| Swap growth | 6.8 GB (recorded, not a limit) |
| First token / total, median | 11.6 s / 26.3 s |
| Roster | `["qwen3.5:4b"]`, single-model path |

Owner's verdict on the H12.AC3 exercise during this run (Mail, a document and Finder, switching about every minute, about 10 minutes): "usable".

The per-sample trace of this run was not kept (the CSV keeps the summary fields the contract requires); H13 keeps the full trace.
