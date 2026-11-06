# Final evaluation on the locked questions (T38)

Run `20261106-114704-7371a3`, 2026-11-06 11:58. One run only: the locked set is never re-run after changes.

* Status: **pilot, below target**
* Hard blockers: 0
* Frozen configuration `config/selected.toml` SHA-256: `c6ba0366c9ec4838cc25e7de3cccc503a584266b1d6b91ca2d2f8d8e993753c1`
* Locked file `eval/locked.json.age` SHA-256: `605cd2b41b5ca02767d0253901585d047f6659324f8b3d939ddff83483839553`

| metric | language | value | count | target |
|---|---|---|---|---|
| recall@5 | en | 0.875 | 7/8 | ≥ 0.85 |
| recall@5 | es | 1.000 | 8/8 | ≥ 0.85 |
| recall@5 | all | 0.938 | 15/16 | ≥ 0.85 |
| mrr | en | 0.938 | 8/8 | ≥ 0.70 |
| mrr | es | 0.938 | 8/8 | ≥ 0.70 |
| mrr | all | 0.938 | 16/16 | ≥ 0.70 |
| faithfulness | en | 0.729 | 8/11 | ≥ 0.90 |
| faithfulness | es | 0.872 | 7/11 | ≥ 0.90 |
| faithfulness | all | 0.796 | 15/22 | ≥ 0.90 |
| answer_relevancy | en | 0.750 | 8/11 | ≥ 0.80 |
| answer_relevancy | es | 0.653 | 7/11 | ≥ 0.80 |
| answer_relevancy | all | 0.705 | 15/22 | ≥ 0.80 |
| citation_validity | en | 1.000 | 11/11 | ≥ 1.00 |
| citation_validity | es | 1.000 | 11/11 | ≥ 1.00 |
| citation_validity | all | 1.000 | 22/22 | ≥ 1.00 |
| refusal | en | 0.000 | 0/1 | ≥ 0.90 |
| refusal | es | 1.000 | 2/2 | ≥ 0.90 |
| refusal | all | 0.667 | 2/3 | ≥ 0.90 |
| questions_passed | en | 0.636 | 7/11 | – |
| questions_passed | es | 0.818 | 9/11 | – |
| questions_passed | all | 0.727 | 16/22 | – |
| fact_correct | en | 0.857 | 6/7 | – |
| fact_correct | es | 0.857 | 6/7 | – |
| fact_correct | all | 0.857 | 12/14 | – |
| summary_coverage | en | 0.500 | 1/2 | – |
| summary_coverage | es | 1.000 | 1/1 | – |
| summary_coverage | all | 0.667 | 2/3 | – |
| adversarial | en | 0.000 | 0/1 | – |
| adversarial | es | 0.000 | 0/1 | – |
| adversarial | all | 0.000 | 0/2 | – |

Below target:
* refusal (en): 0.00 < 0.90
* refusal (all): 0.67 < 0.90
* faithfulness (en): 0.73 < 0.90
* answer_relevancy (en): 0.75 < 0.80
* faithfulness (es): 0.87 < 0.90
* answer_relevancy (es): 0.65 < 0.80
* faithfulness (all): 0.80 < 0.90
* answer_relevancy (all): 0.70 < 0.80
