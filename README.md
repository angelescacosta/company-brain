# Company Brain

**English** · [Español](README.es.md)

A private AI assistant for a small consultancy. The AI runs entirely on a 16 GB MacBook Air M4, with no cloud AI service, and answers only from the company's own documents, citing the source of every fact.

Final project of the **KeepCoding AI Engineering** bootcamp. Slides: [English](docs/presentation/company-brain-presentation-en.pdf) · [Spanish](docs/presentation/company-brain-presentacion-es.pdf).

> Every client and document in this repository is **fictional** or **public**. It holds no real client data.

## The problem

A consultancy works with call transcripts, proposals, contracts and reports. They are confidential, so they cannot be pasted into ChatGPT or other online AI services. The documents stay in the company's Google Drive, as before; Company Brain reads the copy synced to the laptop and does three things without sending any document to an outside AI:

| Mode | What it does |
| --- | --- |
| **Answer** | Answers questions about a client and cites the source passage. If the answer is not in the documents, it says so. |
| **Summarise** | Summarises a call (up to one hour) and downloads it as a Word file. |
| **Draft** | Drafts a proposal in the consultancy's methodology and downloads it as a PDF. |

## Constraints

- €0 extra for cloud AI or APIs: the AI runs locally.
- 16 GB of memory (MacBook Air M4, the only computer available).
- English and Spanish.
- Tested only with fictional (60 documents) and public (17 documents) data.

## Architecture (RAG)

```
When a document is added
  Drive folder ─▶ Docling (PDF, Word, photos with OCR) ─▶ chunks of ~500 tokens
  ─▶ EmbeddingGemma 2 ─▶ PostgreSQL + pgvector

When a question is asked
  Hybrid search (vectors + keywords, Reciprocal Rank Fusion)
  ─▶ mMiniLM reranker ─▶ Qwen3.5-4B in Ollama ─▶ answer with citations
```

- **Model:** Qwen3.5-4B (Apache 2.0). Gemma 4 12B and Qwen3.5-9B were dropped because they exceeded the memory limit with the search models loaded (`reports/benchmark_early.csv`).
- **Embeddings:** Qwen3-Embedding-0.6B, bge-m3 and EmbeddingGemma 2 were compared; they tied on quality and EmbeddingGemma 2 was the fastest (`reports/tuning/`).
- **Reranker:** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (118M parameters, multilingual), chosen over bge-reranker-v2-m3, which did not fit in 16 GB next to the other models.
- **Interface:** Open WebUI, with the assistant as the “Company Brain” model (a Pipe that calls the local API in `src/company_brain/backend.py`).
- **Fine-tuning:** deliberately postponed. With fewer than 50 of the company's own documents and 16 GB, RAG gives more for less.

## Evaluation

66 questions with known answers, half in English and half in Spanish: 44 for tuning (`eval/tuning.json`) and 22 encrypted until the final test, to avoid overfitting.

Metrics: retrieval recall@5 and MRR, correct facts, a citation that supports each fact, refusal when there is no answer, resistance to planted instructions, and an LLM judge (Gemma 4 12B) for faithfulness and relevance. Plus a blind human rating of 20 answers.

Results. The practice set was used to tune the system; the final exam is the 22 locked questions, run once on 6 November 2026 with the frozen configuration (`config/selected.toml`, report in `reports/final_eval.md`):

| Metric | Practice (44 questions) | Final exam (22 questions) | Target |
| --- | --- | --- | --- |
| Questions passed | 41 / 44 (93%) | 16 / 22 (73%) | – |
| Right document in the top 5 (recall@5) | 30 / 30 (100%) | 15 / 16 (94%) | ≥ 85% |
| No figure shown without a source | 44 / 44 | 22 / 22 | 100% |
| Says “I don't know” when there is no answer | 7 / 7 | 2 / 3 | ≥ 90% |
| Correct facts | 25 / 26 | 12 / 14 | – |
| Planted instructions resisted | 3 / 4 | 0 / 2 | – |
| Faithfulness to sources (judge) | 0.89 | 0.80 | ≥ 0.90 |
| Answer relevance (judge) | 0.71 | 0.70 | ≥ 0.80 |
| Hard blockers (client leaks, unsourced figures) | 0 | 0 | 0 |

Blind human rating: 20 answers scored 4.8 out of 5 on average (English 4.9, Spanish 4.7).

Verdict: **pilot, below target.** Safe to use for drafts that a person reviews (it never showed a figure without its source and never mixed clients), not yet for use without review. The weak points are resistance to planted instructions, relevance (0.70), and faithfulness on English answers.

The analysis is in [`notebooks/results.ipynb`](notebooks/results.ipynb) and every result is in `reports/eval_runs.csv`. The test set is on Hugging Face: [AHAcosta/company-brain-eval](https://huggingface.co/datasets/AHAcosta/company-brain-eval).

## Privacy and security

- A per-app firewall (LuLu) keeps the AI off the internet; it is checked at every start.
- Documents stay in the company's Google Drive; the AI only reads the copy the Drive app syncs to the Mac. Backups go to Drive encrypted with `age`, so the storage provider cannot read them.
- Nightly backup, encrypted and signed, with a tested restore.
- OWASP security audit (`reports/security_audit.md`).
- Only datasets and documents whose licence allows commercial use (`LICENCES.md`).

## Layout

| Folder | Contents |
| --- | --- |
| `src/company_brain/` | The assistant: ingestion, search, the three modes, backups, firewall |
| `scripts/` | Evaluation, tuning, test data and maintenance |
| `launchers/` | Double-click buttons: Start AI, Stop AI, Check health… |
| `eval/` | Evaluation questions and their schema |
| `data/` | Expected facts and test document lists |
| `reports/` | Test results, benchmarks and audit |
| `tests/` | Unit and integration tests (pytest) |
| `docs/` | User guide, demo script, decision log and slides |
| `notebooks/` | Results analysis |

## How to run it

Requirements: macOS on Apple Silicon, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com), [Postgres.app](https://postgresapp.com) with pgvector, and Open WebUI.

```bash
uv sync
uv run pytest            # unit tests
```

Set the documents folder in `config/sources.toml` and use the launchers in `launchers/`. The full guide is `docs/USER_GUIDE.md`.

## Credits

The fictional documents are this project's own work. Public documents: CUAD (The Atticus Project, CC BY 4.0), CORD (NAVER Clova, CC BY 4.0), the UK Statistics Authority annual report (Open Government Licence v3.0) and the Ararteko 2024 annual report (CC BY 4.0). Model and library licences are in `LICENCES.md`.

## Licence

Code: [MIT](LICENSE) © 2026 Ángeles Hernández Acosta. The fictional test dataset on Hugging Face is CC BY 4.0. Models, libraries and public documents keep their own licences (`LICENCES.md`).
