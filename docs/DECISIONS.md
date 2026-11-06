# Decisions

Why Company Brain is built the way it is. One entry per decision: what was decided, why, and what was considered instead. The detailed plan is in `PRD.md`; this page is the short version for reviewers and anyone continuing the work.

Newest entries are added at the end. A decision that changes is not deleted: it is marked "Replaced by D<n>".

| ID | Date | Decision | Status |
| --- | --- | --- | --- |
| D1 | 2026-10-16 | Run the AI locally on the MacBook Air M4 | Active |
| D2 | 2026-10-20 | Documents stay in Google Workspace Drive, one folder per client | Active |
| D3 | 2026-10-20 | Only selected folders are read; tax, bank and all others never | Active |
| D4 | 2026-10-20 | No internal or client data in this project; public and fictional only | Active |
| D5 | 2026-10-20 | RAG first; fine-tuning and knowledge graph later | Active |
| D6 | 2026-10-20 | Models: Qwen3.5-9B answers, Qwen3-Embedding-0.6B for search | Active |
| D7 | 2026-10-20 | PostgreSQL with pgvector as the only database | Active |
| D8 | 2026-10-20 | Docling reads the files; chunking follows document structure | Active |
| D9 | 2026-10-20 | Hybrid search plus reranker | Active |
| D10 | 2026-10-20 | No source, no answer: guardrails on every answer | Active |
| D11 | 2026-10-20 | Measure quality on a sealed test set | Active |
| D12 | 2026-10-20 | Per-application firewall instead of a separate service account | Active |
| D13 | 2026-10-20 | Claude Code's access to real data is settled before go-live | Active |
| D14 | 2026-10-20 | Nightly encrypted backup in a separate Drive folder | Active |
| D15 | 2026-10-20 | Methodology from the owner's own notes, not third-party texts | Active |
| D16 | 2026-10-20 | Plan as a PRD: tests first, independent review, owner acceptance | Active |
| D17 | 2026-10-20 | Security audit against OWASP before release | Active |
| D18 | 2026-10-20 | rsc-harness evaluated and not used | Active |
| D19 | 2026-10-20 | Demo deadline 6 November 2026, with a reduced fallback | Active |
| D20 | 2026-10-20 | Documents update incrementally, on demand, at start and nightly | Active |
| D21 | 2026-10-20 | Images are read by on-device OCR; tested with fictional receipts | Active |
| D22 | 2026-10-20 | Test results shown in an offline dashboard that cannot spoil the blind rating | Active |
| D23 | 2026-10-20 | Test isolation guard built on Python audit hooks | Active |
| D24 | 2026-10-20 | Exact folder layout rules for the scanner | Active |
| D25 | 2026-10-20 | Ollama as the command-line program, PostgreSQL 18 | Active |
| D26 | 2026-10-20 | Where models live and how they stay pinned offline | Active |
| D27 | 2026-10-20 | Database roles cannot create anything, not even trusted extensions | Active |
| D28 | 2026-10-21 | How the firewall tests know LuLu blocked a connection | Active |
| D29 | 2026-10-21 | How the launchers run the services | Active |
| D30 | 2026-10-21 | How the fictional corpus is authored (T11, T13) | Active |
| D31 | 2026-10-21 | A small multilingual reranker instead of bge-reranker-v2-m3 | Active |
| D32 | 2026-10-21 | One small answering model, judged by memory pressure and the owner | Active |
| D33 | 2026-10-24 | Which reader converts which file type | Active |
| D34 | 2026-10-24 | Own chunkers, budget counted with a pinned tokenizer | Active |
| D35 | 2026-10-24 | How Ragas runs with local models | Active |

## D1: Run the AI locally on the MacBook Air M4

* Decision: the assistant runs entirely on the owner's MacBook Air M4 (16 GB) through Ollama and Open WebUI. Its AI services have no internet access.
* Why: consultancy documents are confidential. A local model means they are never sent to an AI provider.
* Considered: cloud AI services (simpler and stronger, but documents leave the Mac); a rented cloud GPU (kept only as an optional test budget of USD 20, now out of scope).
* Consequence: model size is limited by 16 GB of memory, so models run one at a time and answers are slower.

## D2: Documents stay in Google Workspace Drive, one folder per client

* Decision: files stay in the business's existing Workspace Drive, laid out as `Clients/<client>/{Transcripts,Proposals,Contracts,Reports}`, plus `Methodology/`. The system reads them only through Google Drive for Desktop, never through an online Drive connector.
* Why: the owner already works there; files in Workspace are private unless shared. One folder per client makes it easy to keep each answer to one client.
* Considered: moving files to the Mac only (loses the existing workflow and Drive's own backup).

## D3: Only selected folders are read

* Decision: an allowlist names the folders the system may read. Tax, bank and every other folder are never read.
* Why: the AI is not needed for those documents, and the safest data is data the system never touches.

## D4: No internal or client data in this project

* Decision: everything is built and tested on public and fictional documents. While the system is in test mode, it ingests only files listed in the test manifests.
* Why: there are no signed clients yet, and a student project should not hold confidential material. This also makes the demo safe to show.
* Consequence: going live with real documents needs a separate go-live plan (see D13).

## D5: RAG first; fine-tuning and knowledge graph later

* Decision: build retrieval-augmented generation (the model answers from retrieved passages) first. Fine-tuning and a knowledge graph are out of scope for this project.
* Why: RAG gives cited, checkable answers from current documents; fine-tuning needs real examples that do not exist yet. A knowledge graph is added only if tests show questions across several documents fail.

## D6: Models

* Decision: Qwen3.5-9B writes the answers (Gemma 4 12B and Qwen3.5-4B as alternatives). Qwen3-Embedding-0.6B turns text into vectors for search, with bge-m3 and EmbeddingGemma 2 (270M text model, released 6 October 2026) as the other candidates. The final choice is made on the test set.
* Why: good English and Spanish, open licences, and they fit in 16 GB. EmbeddingGemma 2 was added as a candidate on 20 October: its published multilingual text score (61.4) is below Qwen3-Embedding's (64.3), so it is tested rather than adopted; it is dropped from the comparison if testing it would delay the schedule.

## D7: PostgreSQL with pgvector

* Decision: one PostgreSQL database (Postgres.app, no Docker) holds documents, text chunks, vectors and the keyword index. Exact vector search first; an approximate index only if speed requires it.
* Why: one well-known database for everything, small enough for a laptop, and it can also hold a knowledge graph later.
* Considered: dedicated vector databases and Neo4j (extra services and memory for no gain at this size).

## D8: Docling reads the files; chunking follows document structure

* Decision: Docling converts PDF, Word, PowerPoint, Excel, email and transcripts, keeping headings, tables and page numbers. Chunks of about 500 tokens follow the structure: sections for proposals and reports, whole speaker turns for transcripts, one clause per contract chunk, tables kept whole or split by rows with the header repeated. Each chunk carries a label such as "Client · Proposal · date · section".
* Why: chunks that follow the document keep their meaning, and page numbers make citations possible.
* Considered: fixed-size and semantic chunking (compared; structure-based chunking won for these document types).

## D9: Hybrid search plus reranker

* Decision: search by meaning (vectors) and by keyword (PostgreSQL full-text, English and Spanish), merge the results, then rerank (with the small multilingual reranker since D31) and keep the best five. Every search is restricted to one client.
* Why: meaning search misses exact names and numbers; keyword search misses paraphrases. Together they find more of the right passages.

## D10: No source, no answer

* Decision: every answer cites its sources (file and page) or says it is not found in the documents. Checks run after writing: citations must exist, numbers, dates and names must match the cited text, and a hallucination classifier (HHEM) flags unsupported claims. Document text is treated as data, never as instructions.
* Why: a confident wrong answer is worse than no answer, especially for fees and contract terms.

## D11: Measure quality on a sealed test set

* Decision: test questions in English and Spanish with expected answers, including questions that must be refused. Part of the set is sealed and used once, at the end. Metrics: whether the right passage is found (recall@5, MRR), faithfulness (Ragas), citation validity, refusals, and a blind rating by the owner.
* Why: tuning on the same questions used for the final score would overstate quality.

## D12: Per-application firewall instead of a separate service account

* Decision: the LuLu firewall blocks Ollama, PostgreSQL and the Python that runs the AI services from the internet. The firewall is checked every time the system starts.
* Why: it gives the "nothing leaves the Mac" guarantee with less setup than a separate macOS service account, which an earlier version of the plan used.

## D13: Claude Code's access to real data is settled before go-live

* Decision: Claude Code, the AI assistant that builds this project, runs under the owner's account and could technically read the owner's files. In this project that is acceptable because only public and fictional data is used. Before any real client document is added, a go-live plan must set the boundary, for example a separate macOS account for Claude Code, or no Claude Code on this Mac.
* Why: the local model never sends data out, but the building assistant is a cloud service. This is stated openly instead of being assumed away.

## D14: Nightly encrypted backup in a separate Drive folder

* Decision: one encrypted backup file per night in `Company Brain Backups/`, next to the documents root, never read by the system. Restore is tested on this Mac (moving to another Mac was removed from the plan on 24 October at the owner's request).
* Why: the database can be rebuilt from Drive, but conversions, settings and evaluation results take time to recreate.

## D15: Methodology from the owner's own notes

* Decision: the consulting style comes from the owner's own notes, in their own words. Published books and samples by other authors (including freely downloadable sample proposals) are not used for training or as search material, and outputs never imitate a named author.
* Why: free to download is not free to reuse; copyright protects the wording, and a small model trained on a few pages tends to repeat them. Methods and ideas can still be applied.

## D16: Plan as a PRD with tests first and independent review

* Decision: the work is defined in `PRD.md` as small tasks with numbered acceptance criteria. Each task's tests are written before its code, property-based tests (Hypothesis) are used where useful, and a task closes only when all tests and linters pass. The plan was reviewed by a second AI (Codex) until no blocking findings remained, then accepted by the owner.
* Why: the owner does not code, so the evidence that something works has to be automatic and recorded.

## D17: Security audit against OWASP before release

* Decision: task T46 audits the finished system on 3–4 November against the OWASP Top 10:2025 and the OWASP Top 10 for LLM Applications 2025, scans dependencies for known vulnerabilities and the repository history for secrets. Any open Critical or High finding blocks the release.
* Tool: Anthropic's reviewed claude-security plugin. Third-party OWASP plugins were considered but had no marketplace review.
* Why: a RAG system adds AI-specific risks, such as instructions hidden in documents and leakage between clients, on top of the usual ones.

## D18: rsc-harness evaluated and not used

* Decision: rsc-harness (recommended by the course, not mandatory) was reviewed and not installed for this project.
* Why: it updates itself from npm in the background by default, wires many hooks into the coding assistant, and overlaps with the PRD workflow, memory and reviews already in place. Adding it 17 days before the deadline would add failure points. It may be tried after the demo on a test project, with auto-update off.
* Borrowed: show the plan before writing anything, and keep project knowledge in the repository (this page).

## D19: Demo deadline 6 November 2026, with a reduced fallback

* Decision: five phases from 20 October to 6 November. If building is more than two days behind on 28 October, the demo switches to a reduced profile without Draft mode and export. Privacy, scope, guardrail, evaluation and security tasks are never dropped.
* Why: a smaller system that is safe and measured beats a larger one that is not.

## D20: Documents update incrementally, on demand, at start and nightly

* Decision: only new, changed, renamed or deleted files are processed, recognised by a fingerprint of their contents. Updates run from an "Update documents" launcher, after "Start AI", and nightly at 01:30 before the 02:00 backup. One lock makes sure an update and a backup never run at the same time. A full rebuild is needed only when the embedding model or passage size changes, and the old index keeps working until the new one is complete.
* Why: re-processing everything after each new file would be slow on a laptop; a fixed schedule means the owner does not have to remember.

## D21: Images are read by on-device OCR; tested with fictional receipts

* Decision: photos and scans are read with Apple's on-device text recognition (Vision, through Docling). iPhone HEIC photos are converted in memory. The test data includes fictional receipts, as clean images and as simulated phone photos, a whiteboard photo, five public CORD receipts and, optionally, four phone photos of printed fictional receipts taken by the owner. Amounts and dates are scored field by field.
* Why: consultancy material includes photos and scans, and misread numbers are the main risk. Real receipts are financial records and are never used (D3).
* Considered: searching images directly with a multimodal embedding model (EmbeddingGemma 2); it can find a photo but cannot quote the amount, so text recognition is still needed for citations and value checks.

## D22: Test results shown in an offline dashboard

* Decision: a static page, opened from a "Show test results" launcher, shows search quality per embedding model, answer models side by side, speed and memory, image-reading accuracy, a browser of the tuning questions, and the final scorecard against targets. It has no JavaScript and no links, works offline, and is built only from saved result files.
* Why: the owner cannot read CSV files comfortably, and the comparison between models is the core of the evaluation and of the course presentation.
* Safeguards: until the owner's blind rating is finished and unblinded, the page holds no answers, per-model answer scores or ratings, so the owner cannot learn which model wrote which answer. The locked final questions never appear; only their totals. A failed rebuild shows "out of date", never old results as if current. Publishing a copy online is a separate decision for the owner.

## D23: Test isolation guard built on Python audit hooks

* Decision: while the tests run, a Python audit hook refuses file access under `~/CompanyBrainData`, `~/CompanyBrainExports` and the Drive mount (`~/Library/CloudStorage`, plus the root recorded by T10), connections to the real service ports (Open WebUI 8080, backend 8765; tests use 18080 and 18765), and subprocesses whose arguments or environment name a real path, the `company_brain` database or a non-test Keychain entry. Child processes started by the tests guard themselves when they import the project code. Allowlisted tests name each resource and each write exactly (`<resource>:<what>`).
* Why: it catches access in the test process and in subprocesses without depending on every future module remembering to check.
* Limits: it is a cooperative guard in our own code. Metadata checks (does a file exist) are not blocked, and a program that does not import our code, or deliberately bypasses the hook, is not stopped.
* Also: CHK4 lints only the shell files that exist, so it runs before the first launcher is written (T6).

## D24: Exact folder layout rules for the scanner

* Decision: client files count only when they sit directly in `Transcripts`, `Proposals`, `Contracts` or `Reports` (fictional clients also `Receipts` and `Receipts/Owner photos`). `Methodology/` and `Test data (fictional)/Public/` may have subfolders. Client folders named `public` or `shared`, or containing `:`, are refused, because those names are used for the public and shared scopes. A linked file is followed only when its real location is itself in an allowed folder, and it takes the scope of that real location; linked folders are never followed. Google pointer files (`.gdoc`, `.gsheet`, `.gslides`) are reported separately for T16. Old Office formats (`.doc`, `.ppt`, `.xls`) are reported as unsupported, because Docling reads only the current formats.
* Why: anything outside these rules is reported for the owner to fix instead of guessed, so a misplaced file can never land in the wrong client's scope.

## D25: Ollama as the command-line program, PostgreSQL 18

* Decision: Ollama is installed from its signed command-line release (`ollama-darwin.tgz`, in `~/.local/opt/ollama-0.40.6`), not the menu-bar app. Postgres.app 2.9.6 is installed with PostgreSQL 18 only, which bundles pgvector 0.8.6.
* Why: the menu-bar app starts itself at login and updates itself, which would break version pinning (T2) and the firewall rules tied to its path (T4); the services are started by our own launchers instead (T6). The single-version Postgres.app download is a quarter of the size of the all-versions one.

## D26: Where models live and how they stay pinned offline

* Decision: Ollama models stay in `~/.ollama/models`, Hugging Face models in `~/.cache/huggingface` at pinned revisions, Docling's models in `~/.cache/docling/models`, and the Ragas tokenizer files in `~/.cache/tiktoken`. Their digests, revisions and folder hashes are in `config/models.toml`. Because HHEM loads its base model (flan-t5-base) by the name "main", the cache's "main" pointer is set to the pinned revision.
* Why: models are public files, not confidential data, so they stay out of `~/CompanyBrainData` and the tests can check them. Every check runs in a process that cannot reach the internet, which proves nothing is fetched at run time.
* Note: Docling picks IBM's granite-vision 4.1 (4B) for chart reading. It is large, so it only runs during document conversion, never alongside an answering model.

## D27: Database roles cannot create anything, not even trusted extensions

* Decision: `brain_app` and `brain_test` own only the `brain` schema in their own database. They have CONNECT and TEMPORARY rights but not CREATE on the database. `pg_hba.conf` allows each role only its own database over 127.0.0.1 with a password, and allows the owner's account only through the local socket. pgvector is installed by the owner's setup script.
* Why: the T5 test found that CREATE on a database lets a role install "trusted" extensions such as hstore, which AC2 forbids.

## D28: How the firewall tests know LuLu blocked a connection

* Decision: LuLu keeps no log of its decisions (none in the macOS log either), so a probe counts a denial as recorded only when both hold: LuLu's rule list (`/Library/Objective-See/LuLu/rules.plist`, read-only) has an enabled block-all rule for that exact program, and the attempt failed the way LuLu fails it ("Bad file descriptor" on connect). A failed name lookup or timeout never counts, because the network could cause it. Ollama and PostgreSQL rules are keyed by their signing team and checked against `config/install_log.toml`. Python is unsigned, so its rule is by path.
* Why: this keeps review 4's rule (a failed operation alone is not proof) without a log LuLu does not have. During setup the Mac's own name lookups failed for a short while, and the test correctly reported "not applicable" instead of passing.
* Note: this network has no IPv6 internet, so the IPv6 probes are "not applicable"; nothing can leave over IPv6 while that holds. PostgreSQL ships the dblink extension, which only the owner's admin account could install (D27); a manual probe through it on 21 October was refused by LuLu.

## D29: How the launchers run the services

* Decision: the services run as per-user launchd jobs whose files live in `~/CompanyBrainData/launchd`, not in `~/Library/LaunchAgents`, so nothing starts at login; the owner starts the AI with Start AI. Start checks LuLu first (running, network filter on, pinned settings, a block rule for each program). Online, it then starts PostgreSQL and Ollama, runs the T4 probes (the Ollama probe needs the Ollama server), and stops everything again if a probe gets out or is inconclusive; Open WebUI starts last. Offline, LuLu's state is the whole check. Service output goes to /dev/null, because third-party logs could hold content (T8). Stop also stops copies started by hand on the same ports.
* Why: no administrator rights, no automatic start that skips the firewall check, and a probe that is real rather than assumed. The probes' curl control runs before the Ollama attempt, because after a blocked attempt macOS answers lookups for that name slowly for a while (seen twice during T4).
* Note: tests run their own copies (launchd names `com.companybrain-test.*`, a scratch PostgreSQL cluster, ports 21434, 15432 and 18080); the guard refuses the real names, folder and ports under test.
* Update 2026-10-24 (T20U): `scripts/brainctl` is the one command behind every launcher; Start AI also arms the nightly 01:30 update job, which stays armed until logout, because nothing is installed to start at login.
* Update 2026-10-24 (T29): a fourth service, the assistant (`company_brain.backend`, port 8765, tests 18765), starts after the firewall check and before Open WebUI. It loads its models on the first request, so Start AI stays fast.

## D30: How the fictional corpus is authored (T11, T13)

* Decision: the corpus is generated from authored records (`src/company_brain/corpus_data.py`) by code, so decisions and action items are written into the transcripts from the same records that become `facts.json`. Output is byte-for-byte reproducible (fixed document dates, fixed zip timestamps, seeded scan noise), so the test rebuilds it and compares it with the committed manifest. Folder and file names use plain letters (no accents), because Drive and macOS may store accented names in different Unicode forms and the guarded reader compares paths exactly. The authoring scripts never open a file they did not record: an unrecorded file with the same name stops the script.
* Why: ground truth that cannot drift from the text, and manifests that can be checked without trusting the Drive copy.
* Note: `openpyxl` and `pypdfium2` were already installed through Docling; they are now declared directly because the corpus code and tests use them. `types-openpyxl` is pinned to a release older than two weeks.

## D31: A small multilingual reranker instead of bge-reranker-v2-m3

* Decision: the reranker is `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (about 118 million parameters, Apache 2.0, trained on a 14-language translation of MS MARCO that includes Spanish), pinned at revision 1427fd65. bge-reranker-v2-m3 stays pinned so it can be compared, but is not loaded by the product.
* Why: the early benchmark (T7) showed that with bge-reranker-v2-m3 and the embedding model loaded, the Mac already used 10.7 GB before any answering model, even with Chrome closed, so every model went over the memory and swap limits. The smaller reranker takes 1.7 GB instead of 3.7 GB and ranks 40 passages in about 0.5 s instead of 5.3 s. The owner chose it (H12) after Claude explained the trade-off: it may order subtle passages less well.
* Considered: bge-reranker-v2-m3 on the CPU (1.1 GB, but about 7 s for 40 passages and still too much swap in a trial); loosening the limits (the Mac would feel slow); no reranker (more wrong passages); gte-multilingual-reranker-base (needs remote code to load); jina-reranker-v2-base-multilingual (non-commercial licence).
* Note: downloaded with curl into the Hugging Face cache, because the firewall blocks Python; the weights' SHA-256 matched the hash Hugging Face publishes. Its quality is measured in T35 stage 3 (reranker on or off) and T34.

## D32: One small answering model, judged by memory pressure and the owner

* Decision: the assistant uses one answering model, Qwen3.5-4B (the single-model path). A setup passes when macOS memory pressure never reaches critical and the owner finds the Mac usable; memory used and swap growth are still measured and reported, but are no longer pass or fail limits. The owner is advised to close Chrome while asking questions, for faster answers.
* Why: this MacBook Air (16 GB) already uses 6.4 to 8.5 GB before any model loads, so no setup met the old limits (14 GB, 1 GB swap growth): not the CPU reranker, closing Chrome, the smaller reranker (D31) or loading the helpers only while searching. The owner has no other computer, and rejected every paid cloud option on cost. In a hands-on test the small model was "a bit slow" with Chrome closed (pressure normal, no swap growth) and "quite slow" but working with Chrome open (pressure warning, never critical).
* Considered: Gemini through Google Cloud, and the owner's own open model on Cloud Run (both rejected on cost); the larger models Qwen3.5-9B and Gemma 4 12B (slower still on this Mac); a leaner model file format (kept as a fallback if 4B misses a time target).

## D33: Which reader converts which file type

* Decision: Docling, with local models only, converts PDFs and slides, with Apple Vision OCR for scanned pages. Word files, spreadsheets and transcripts are read directly with python-docx, openpyxl and a small WebVTT parser. Image files (receipts, whiteboard photos) are read with Apple Vision directly, and the words are regrouped into rows by position, allowing for a tilted photo. No chart-reading model is loaded: the test charts print their values on the page or keep them in cells.
* Why: the direct readers give exact provenance (paragraph number, cell range, cue number and time) that the citations and the tests rely on. On receipts, Docling's layout step merged lines into blocks and separated each label from its amount, so totals and VAT were misread; Apple Vision lines regrouped by position read every receipt field exactly, clean and photographed. The chart model (Granite Vision 4B) would need several more gigabytes on a Mac that is already at its limit (D32).
* Considered: Docling for every type (worse provenance for Word and spreadsheets, receipt lines merged); upscaling images before OCR (fixed one handwriting misread but lost an accent, so not adopted).

## D34: Own chunkers, budget counted with a pinned tokenizer

* Decision: the chunkers in `src/company_brain/chunkers.py` work on the converted elements from T17 and count every budget with the pinned tiktoken encoding o200k_base, instead of Docling's HybridChunker with the embedding model's tokenizer.
* Why: T17 keeps its own elements with exact provenance (D33), which HybridChunker would not see; the embedding models exist on the Mac only as Ollama files, so their Hugging Face tokenizers are not available offline; and every embedding model accepts far more than the largest budget (800), so a small difference between tokenizers cannot truncate a chunk.
* Considered: downloading each embedding model's tokenizer (more files to pin and licence, for no practical gain).

## D35: How Ragas runs with local models

* Decision: Ragas 0.4.3 is imported with a small stand-in for `langchain_community.chat_models.vertexai` (Ragas only uses that class for a type check), and the judge (Gemma 4 12B) and the relevancy embeddings (bge-m3) are reached through the local Ollama's OpenAI-compatible endpoint with `langchain-openai`, which is already installed.
* Why: the pinned langchain-community 0.4.2 removed both the Vertex AI chat model Ragas imports and the Ollama chat model; this keeps the Ragas metrics the plan names without adding or downgrading packages. Everything stays on 127.0.0.1, under the LuLu-blocked Python (T34.AC3).
* Considered: downgrading langchain-community (conflicts with langchain 1.4), installing langchain-ollama (a new package), and writing the metrics ourselves (no longer Ragas).

## D36 Restore switches over on the same schema (T40), 2026-10-24
The PRD says restore goes "into a new data folder and database". The assistant's database role cannot create databases or rename schemas (least privilege, T5), and giving it those rights would widen what a fault could do. Restore therefore takes a safety copy of the current data first (a dump of the `brain` schema and a copy of Open WebUI's data), replaces the tables from the archive in one transaction, reconciles deletions and checks the result; any failure puts the safety copy back. The acceptance criteria (identical data, nothing changed on a bad key, previous data kept on a failed switch-over) are met and tested.

## D37 Dashboard passages are cut by element position (T34D), 2026-10-24
T34D says passage text is cut from the converted Markdown copy using the chunk ID and a character span. The evaluation records each passage's source spans as element positions (cue, paragraph, row or page), as stored with each chunk since T18, not character offsets. The dashboard therefore keeps its own converted copy of each listed document (the elements with their positions, as JSON, with a conversion record of its hash), and cuts the passage from the elements whose position matches. The protections are unchanged: text never comes from the result records, the copy's hash is checked, and the source is read only through the guarded reader with its manifest hash.

## D38 How the full benchmark times each mode (T33), 2026-10-25
Answer and Draft are sent to the running assistant's own service exactly as the chat window sends them and timed until the checked answer comes back; the chat window shows that answer whole, so time to first token equals total time. The test documents hold no call near an hour long (the fictional calls are about 170 words), so Summarise runs the product's own conversion, summary, output gate and Word and PDF export in the benchmark process on a fictional one-hour call of about 9,000 words built from fixed sentences (`benchmark.long_transcript`); the search and document choice before it take about a second and are left out. Each warm run uses a new request (another client, or another call), so no answer comes from a cache. A cold run starts with every Ollama model unloaded; the assistant's in-process reranker and claim checker stay loaded, as they do once Start AI has answered once. Memory and pressure are sampled per run under T7's contract. The benchmark's chats and drafts are fictional and stay in the test-mode assistant.

## D39 Summaries keep the five main items per list and read long documents in larger parts (T33), 2026-10-25
The first full benchmark found that Summarise could not finish a dense one-hour call: the model copied every point of a part, its list outgrew the 1,200-token output limit, the JSON was cut off and the whole summary failed. Three changes, each with a test written first: a part whose answer is cut off is read again as two halves; each part keeps at most the five most important items per list (asked in the instructions and enforced by the JSON schema and the code); and a part is now up to 3,000 tokens instead of 1,500, so a one-hour call is four parts (each costs about 40 seconds whatever its size; prompt, part and answer still fit the 8,192-token window). The test documents are all shorter than one part, so their summaries are unchanged; T35's summary coverage measures it again. Result: the one-hour summary went from failing, then 7.7 minutes cold and a 299-second warm median (a pass by under a second), to 3.0 minutes.

## D40 Backups are signed with a key derived from the recovery key (T46, security scan F1), 2026-10-25
The security scan showed that age encryption alone does not prove who made a backup: the recipient is public, so anyone who could write to the Drive backups folder could produce an archive the recovery key opens. Options were a separate signing key in the Keychain (lost with the Mac, so a restore on a new Mac would fail), a passphrase recipient (the nightly backup would need the passphrase), or a key derived from the recovery key. The last is chosen: Set up backups derives an HMAC key from the recovery key and keeps it in the Keychain for the nightly backup, and restore derives it again from the key the owner types, so the paper key alone still restores anywhere. The manifest (which lists every member's SHA-256) and the encrypted list of deleted clients are signed; anything unsigned or signed with another key is refused, with nothing changed. Owners who set up backups before this run Set up backups once more and paste the recovery key; until then no backup is made and Check health says so. After a restore only the project's own Pipe stays in Open WebUI, from current source with this Mac's token. Refusing archive names dated in the future was not added: a forged one fails its signature anyway.


## D41 The owner scores answers in a rating page, with a written rubric (T36, H6), 2026-10-25
The plan names "a sheet" without a format and refers to a rubric it does not spell out. The owner chose a page over a spreadsheet after seeing both with an example. "Rate answers" opens a self-contained page (no links, no files from elsewhere, a content policy that allows only its own inline script and style). Each answer has five buttons, and "Save my scores" stays disabled until every answer has one; it saves `company-brain-scores.json` to Downloads, and the launcher reads the newest such file, checks it belongs to this pack and unblinds. The rubric, from H6's "accuracy, completeness, tone and language": 5 ready to send; 4 small edits; 3 usable with work; 2 mostly wrong or unhelpful; 1 wrong, misleading or unusable; for a question the documents do not answer, a clear "I could not find this" is the right answer.

## D42 Stage 3 keeps the reranker on, accepting a small Spanish ranking loss (T35), 2026-10-25
By the written rule (recall@5, then MRR, then speed) stage 3's winner is reranker off: recall@5 is 100% either way (15 English and 15 Spanish answerable questions), English MRR is 0.967 both ways, and Spanish MRR is 1.000 off against 0.967 on, because with the reranker one Spanish question's best passage comes second instead of first. The reranker therefore lowers one measure in one language, which T35.AC5 asks to decide here. Decision for Spanish and English: keep the reranker and accept the loss. Reason: stage 3 measures passage order only, while the reranker also carries the relevance floor that answers "not found" without calling the model and adds the draft or signed counterpart of an agreement (T22, T34's evidence coverage); turning it off would remove both without measuring them. Stage 4 and the frozen configuration (T37) use the reranker on. Note on stages 1 and 2: every option reached recall@5 of 100% and MRR of 0.967, so with 30 answerable questions they do not separate the models or chunk budgets on quality; EmbeddingGemma 2 270M and budget 500 won on search speed (0.24 s against 0.31 s for bge-m3), and 500 against 800 by the tie rule.

## D43 Freeze found the wrong index active; the frozen settings now drive indexing (T37), 2026-11-06
When freezing, the active index was the bge-m3 one, not T35's winner (EmbeddingGemma 2, budget 500): `index_documents` defaulted to bge-m3 and every "Update documents" (including the one after Start AI) re-activated it, so the 4 November stage 4 rerun and the rating pack used bge-m3. The other answer-side settings were identical. Fix: `scripts/freeze.py` writes `config/selected.toml` and `index_documents` now takes its model and budget from it, which re-activated the EmbeddingGemma 2 index. The freeze uses the 25 October stage 4 run, made with exactly the frozen retrieval (41/44, 0 hard blockers); the owner's scores (mean 4.8) rate the same answering model and prompts. The T37 benchmark re-run is replaced by the T33 full benchmark, which used bge-m3, the largest embedding model, as the worst case for memory and time; EmbeddingGemma 2 is smaller. Also corrected: slide 7, its notes and the READMEs named bge-reranker-v2-m3 as the filter; the reranker in use is mmarco-mMiniLMv2 (D31).

## D44 The locked run was started twice; the first attempt recorded nothing (T38), 2026-11-06
The first locked run answered and judged the 22 questions, then stopped with an error before writing anything: the row writer refused the "locked" split. No metric, answer or per-question result was shown or saved (only progress lines such as "judged 5 of 15"), and nothing in the system changed between the two attempts apart from that write step. The run was repeated once with the same frozen configuration; the report is from the second attempt. The seal access log shows both reads.

## D45 The source window shows the cited part of the passage (display only), 2026-11-06
The excerpt sent to the chat's source window was the first 400 characters of the passage, so in the Pellmore contract the fee clause was cut off. It is now 500 characters starting shortly before the first value (three or more digits) that the citing facts quote, matched with any separator (36.500 in a Spanish answer, 36,500 in the source); otherwise the start of the passage as before. Answers, retrieval and the gate's checks are unchanged, so the T38 locked result still stands.
