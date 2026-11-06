# Security audit (T46)

Date: 24 October 2026. Scope: the repository code and the fictional and public fixtures; never the Drive folder or real data. Lists: OWASP Top 10:2025 and the OWASP Top 10 for LLM Applications 2025 (`config/owasp_categories.toml`).

How it was done:
- **Automated scans.** `scripts/security_audit.sh` writes its results to `reports/security/`. `reports/security/run.json` records the commit, the `uv.lock` hash, the tool versions and the preflight. The scanners run on a separate Python 3.13, `.../cpython-3.13.16-macos-aarch64-none/bin/python3.13`. LuLu allows that program only, a rule added by the owner on 24 October. The AI Python 3.12 is still blocked: the probes pass and the rule check reports no problems.
- **Code review.** Claude reviewed the code by hand against every category below.
- **Plugin scan.** On 25 October the owner enabled workflows and the claude-security plugin (0.12.0) scanned the whole repository at medium effort (revision 1e888ac with uncommitted changes; 10 components, 31 researchers, all returned; every top-level directory scanned or deliberately skipped: `assets/`, `reports/`, `reviews/` and most of `tests/`). Each candidate was challenged by a three-verifier panel. 5 findings survived (1 Medium, 4 Low; none Critical or High), stamped `verified`. The full report stays out of git (`CLAUDE-SECURITY-20261025-085139/`). They are F-02 to F-06 below, all fixed with tests written first. The scan paused once at the usage limit and resumed from its saved results.
- **Correctness of verdicts.** Whether each verdict is right is a review judgement. The tests check completeness and run the linked regression tests (`tests/test_security_audit_report.py`).

## Results of the automated scans

- **Dependencies (AC2).** pip-audit 2.9.0 checked 188 locked packages. Pillow 12.3.0 had 37 advisories (5 Critical, 21 High, 11 Medium). All 37 are `fixed` by Pillow 13.0.0, which needed Docling 2.142.1. The conversion and OCR tests, slow ones included, pass with the new versions, and the OCR results are unchanged. Pillow 13 encodes one generated fixture photo slightly differently ("Recibo 8 aparcamiento photo.jpg"). It was regenerated and relisted with its new hash, and the repository keeps canonical copies of the listed images (`tests/fixtures/images/`).
- **Dependency exceptions.** Two advisories have no fixed version. Each is an `accepted_exception` until 30 November 2026, with its exposure written in `config/security_exceptions.toml`:
  - diskcache, PYSEC-2026-2447 (High): no cache directory is ever configured.
  - ragas, PYSEC-2026-3046 (High): only the multi-modal helpers are affected, and they are not used.
- **Dependency status.** Nothing is `open`. The statuses are in `reports/security/dependency_status.json`.
- **Secrets (AC4).** The working tree and every commit on every ref give 496 findings. Each is a reviewed non-secret in `.secrets.baseline`, with its reason:
  - 116 SHA-256 file hashes, model digests and index IDs;
  - the name of PostgreSQL's hashing method;
  - a throwaway password for a generated test PDF;
  - a model name;
  - the PRD's sentence about the baseline;
  - the fake key planted by the scan's own test.

  No real secret was found. The audit's own output folder (commit and lock hashes, public advisory data) is skipped by path. A test checks that the same key anywhere else still fails.

## Verdicts

| ID | Category | Verdict | Evidence |
|---|---|---|---|
| A01:2025 | Broken Access Control | finding F-06 | scripts/secret_scan.py:42 (copies named here, never from git); tests/test_secret_scan.py::test_hostile_path_names_never_write_outside_the_copy; src/company_brain/backend.py:159 (every request is checked against the chat's client); tests/test_backend.py::test_token_binding_and_modes; tests/test_retrieval_pbt.py; tests/test_backend.py::test_downloads_are_served_only_from_the_export_folder |
| A02:2025 | Security Misconfiguration | finding F-04 | src/company_brain/services.py:456 (data folder and Open WebUI database private); tests/test_private_data.py; config/openwebui.env checked by tests/test_privacy_config.py; services listen on 127.0.0.1 only, tests/integration/test_loopback.py; LuLu rules tests/test_lulu_rules.py |
| A03:2025 | Software Supply Chain Failures | checked, no finding | uv.lock with hashes; models pinned by digest config/models.toml:5; reports/security/dependency_status.json (37 fixed, 2 accepted exceptions, none open) |
| A04:2025 | Cryptographic Failures | checked, no finding | backups encrypted with age to the public key only src/company_brain/backup.py:237; tests/integration/test_backup.py::test_archive_is_encrypted_complete_and_listed; token from secrets and compared in constant time src/company_brain/backend.py:224 |
| A05:2025 | Injection | checked, no finding | SQL is parameterised throughout; the two formatted statements use fixed table names and placeholders src/company_brain/client_delete.py:91; page text escaped tests/test_dashboard.py::test_hostile_text_and_substitutions_are_inert |
| A06:2025 | Insecure Design | checked, no finding | test-mode content guard src/company_brain/content_guard.py:148; isolation of tests from real resources tests/test_isolation_pbt.py; backup and restore keep the previous data on failure tests/integration/test_restore_roundtrip.py::test_failed_switch_keeps_the_previous_data |
| A07:2025 | Authentication Failures | finding F-05 | src/company_brain/services.py:329 (a port held by another program is never taken for ours); tests/test_port_owner.py; Open WebUI's own sign-in (tests/integration/test_sources_display.py); service token file readable by the owner only tests/test_backend.py::test_token_file_is_private |
| A08:2025 | Software or Data Integrity Failures | finding F-02 | backups signed, restore refuses others src/company_brain/restore.py:195; tests/integration/test_restore_roundtrip.py::test_a_backup_not_signed_by_this_assistant_is_refused; listed bytes only, digest checked src/company_brain/content_guard.py:148; archive manifest checked before restore tests/integration/test_restore_roundtrip.py::test_wrong_key_or_damage_changes_nothing; updates install offline from verified downloads tests/integration/test_update_rollback.py::test_a_failing_update_rolls_back |
| A09:2025 | Security Logging and Alerting Failures | checked, no finding | logs carry no document content tests/test_logging_pbt.py; reads of the sealing key are logged tests/test_sealing.py; Check health reports backup, update and firewall problems src/company_brain/backup.py:373 |
| A10:2025 | Mishandling of Exceptional Conditions | checked, no finding | no stack trace reaches the chat src/company_brain/backend.py:284; plain-English errors tests/test_pipe.py; interrupted backups, updates and restores are recovered tests/integration/test_update_rollback.py::test_an_interrupted_update_is_finished_or_rolled_back |
| LLM01:2025 | Prompt Injection | finding F-03 | planted passages left out of answers and drafts, counterparts included src/company_brain/answer.py:87; sources wrapped as data in the prompt src/company_brain/answer.py:45; planted passages left out of drafts src/company_brain/draft.py:169; tests/test_injection.py (30 cases across the three modes) |
| LLM02:2025 | Sensitive Information Disclosure | checked, no finding | each chat bound to one client src/company_brain/backend.py:159; retrieval limited to the chat's scope src/company_brain/retrieval.py:58; leak checks in the evaluation tests/test_eval_run.py; test mode only real data never read tests/test_entry_points.py |
| LLM03:2025 | Supply Chain | checked, no finding | models pinned by digest config/models.toml:5 and checked tests/test_install_log.py; licences recorded src/company_brain/licences.py:37 |
| LLM04:2025 | Data and Model Poisoning | checked, no finding | only manifest-listed documents enter src/company_brain/content_guard.py:148; planted instructions filtered src/company_brain/draft.py:169; no training or fine-tuning in this system |
| LLM05:2025 | Improper Output Handling | checked, no finding | every output passes the gate src/company_brain/guardrails.py:325; tests/test_gate_coverage.py; dashboard escapes and breaks URLs tests/test_dashboard.py::test_hostile_text_and_substitutions_are_inert |
| LLM06:2025 | Excessive Agency | checked, no finding | the model has no tools and cannot act: it returns JSON that the code checks src/company_brain/answer.py:45; the only files written are exports in the export folder tests/test_export.py |
| LLM07:2025 | System Prompt Leakage | checked, no finding | the prompts hold no secret, key or access rule (src/company_brain/answer.py:45); access rules are enforced in code, not in the prompt src/company_brain/backend.py:159 |
| LLM08:2025 | Vector and Embedding Weaknesses | checked, no finding | every vector search filtered by the chat's scope src/company_brain/retrieval.py:89; tests/test_retrieval_pbt.py |
| LLM09:2025 | Misinformation | checked, no finding | facts need sources, values checked against them src/company_brain/guardrails.py:325; tests/test_guardrails_pbt.py; not-found answer instead of a guess tests/test_answer_mode.py |
| LLM10:2025 | Unbounded Consumption | finding F-01 | src/company_brain/backend.py:31; tests/test_backend.py::test_oversized_requests_are_refused_before_reading; output length capped src/company_brain/answer.py:25; Pipe timeout src/company_brain/openwebui_pipe.py:42 |

## Findings

### F-01: The assistant's service read request bodies of any size
- Severity: Medium
- Rationale: The local service read as many bytes as the request claimed. Only Open WebUI holds the service token, so this was reachable only through the chat window, for example by pasting a huge text. That could exhaust the Mac's memory (LLM10), but it exposes no data and is local only, so the severity is Medium.
- Status: fixed
- Fix: requests over 1 MB, or with a negative or unreadable length, are refused before they are read (src/company_brain/backend.py:31). This is far above a question with its chat history.
- Test: tests/test_backend.py::test_oversized_requests_are_refused_before_reading
- Before: failed (1 failed, without the fix)
- After: passed (1 passed)

### F-02: A backup anyone could encrypt was restored as genuine (plugin scan F1)
- Severity: Medium
- Rationale: age encrypts to the public key in `config/backup_recipient.txt`, so a file the recovery key opens says nothing about who made it, and the manifest's checksums sat inside the same file. Someone who could write to the owner's Drive backups folder could have had a forged archive restored, replacing the assistant's data and Open WebUI's database, whose Functions Open WebUI runs. It needs access to the owner's Google account and the owner choosing to restore, so the severity is Medium.
- Status: fixed
- Fix: each backup's manifest and the encrypted list of deleted clients carry an HMAC-SHA256 made with a key derived from the recovery key (src/company_brain/signing.py:26). Set up backups keeps that key in the Keychain; restore derives it again from the key the owner types, so a restore on a new Mac needs nothing else, and an archive or list without a valid signature is refused (src/company_brain/restore.py:195, :112). No backup is made before signing is set up. After a restore only the project's own Pipe stays in Open WebUI, from current source, and every Tool is removed (src/company_brain/pipe_install.py). Not done: refusing archive names dated in the future; a forged one is refused by its signature anyway.
- Test: tests/integration/test_restore_roundtrip.py::test_a_backup_not_signed_by_this_assistant_is_refused
- Before: failed (the signing module did not exist; also test_a_forged_deleted_clients_list_is_ignored, test_restore_keeps_only_this_projects_pipe, tests/integration/test_backup.py::test_no_backup_is_made_before_signing_is_set_up, tests/test_backup_signing.py, tests/test_setup_backup_key.py)
- After: passed (all of them; backup, restore and client deletion: 20 passed)

### F-03: Draft mode's filter for planted instructions skipped counterpart passages (plugin scan F2)
- Severity: Low
- Rationale: the filter for passages addressed to the assistant ran only on the kept passages, not on the draft or signed counterparts the reranker adds back, so a planted figure could reach a proposal as a sourced fact. It needs a hostile document in the owner's own files and the model following it.
- Status: fixed
- Fix: one filter for Answer and Draft modes over kept passages and counterparts; with only planted passages left, Answer mode gives the not-found message without calling the model (src/company_brain/answer.py:87).
- Test: tests/test_draft_mode.py::test_a_planted_counterpart_is_left_out_too
- Before: failed (also tests/test_answer_mode.py::test_a_passage_addressed_to_the_assistant_never_reaches_the_model)
- After: passed

### F-04: The service token was readable by other accounts on the Mac (plugin scan F3)
- Severity: Low
- Rationale: the token file is private, but the Pipe's copy of the token sits in Open WebUI's database, which had default modes (folders 0755, database 0644). Another account on the same Mac could have read it and queried every client's documents. The owner has no other account.
- Status: fixed
- Fix: Start AI makes the data folder and Open WebUI's folder 0700 and the database 0600; the Pipe installer sets 0600; the service refuses to start with an empty token (src/company_brain/services.py:456, src/company_brain/backend.py:293).
- Test: tests/test_private_data.py
- Before: failed (3 failed)
- After: passed

### F-05: Start AI took any program on a service's port for the service (plugin scan F4)
- Severity: Low
- Rationale: readiness only checked that something answered on the loopback port, so another account binding a port first could have received the owner's chat-window password, the token or the database password. It needs another account on the Mac.
- Status: fixed
- Fix: a service is ready only when the program listening is that service's own, run by the owner; Start AI refuses to start when another program holds a port (src/company_brain/services.py:329). Not done: requiring SCRAM in the database client; the refused start covers the same attack.
- Test: tests/integration/test_launchers.py::test_start_refuses_when_another_program_holds_a_port
- Before: failed (also tests/test_port_owner.py, 5 failed)
- After: passed (tests/integration/test_launchers.py: 12 passed)

### F-06: The secret scan rebuilt git paths on disk (plugin scan F5)
- Severity: Low
- Rationale: names from `git ls-tree` were joined onto the temporary folder, so a hostile repository with `..` entries could have overwritten the owner's files. The scan is only ever run on this project.
- Status: fixed
- Fix: copies are named by a counter in the temporary folder, the original path is kept only for the report, and linked files are skipped (scripts/secret_scan.py:42). The real scan still reports no unreviewed finding.
- Test: tests/test_secret_scan.py::test_hostile_path_names_never_write_outside_the_copy
- Before: failed
- After: passed
