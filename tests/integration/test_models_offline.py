"""T3.AC2: each model answers a tiny request with Hugging Face offline mode set.

Each check runs in a child process that cannot reach the internet (only this
Mac), so a model that tried to download anything would fail.
"""

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
MODELS = tomllib.loads((REPO / "config" / "models.toml").read_text())
EMBEDDING = sorted(MODELS["embedding"])
GENERATIVE = sorted(name for name in MODELS["ollama"] if name not in MODELS["embedding"])


def _ok(result) -> None:  # type: ignore[no-untyped-def]
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith("OK"), result.stdout[-2000:]


def test_child_really_is_offline(run_offline) -> None:  # type: ignore[no-untyped-def]
    result = run_offline(
        """
        import urllib.request
        try:
            urllib.request.urlopen("https://huggingface.co", timeout=5)
        except Exception as error:
            print("blocked", type(error).__name__)
            print("OK")
        """
    )
    _ok(result)


@pytest.mark.parametrize("name", GENERATIVE)
def test_generative_model_answers(run_offline, name: str) -> None:  # type: ignore[no-untyped-def]
    result = run_offline(
        f"""
        import ollama
        client = ollama.Client(host="http://127.0.0.1:11434")
        reply = client.generate(
            model={name!r}, prompt="Reply with the word yes.", think=False,
            options={{"num_predict": 8, "temperature": 0}}, keep_alive=0,
        )
        assert reply.response.strip(), "empty answer"
        print("OK")
        """
    )
    _ok(result)


@pytest.mark.parametrize("name", EMBEDDING)
def test_embedding_model_answers(run_offline, name: str) -> None:  # type: ignore[no-untyped-def]
    result = run_offline(
        f"""
        import math, ollama
        client = ollama.Client(host="http://127.0.0.1:11434")
        vectors = client.embed(model={name!r}, input=["hola", "hello"], keep_alive=0).embeddings
        assert len(vectors) == 2 and len(vectors[0]) > 100
        assert all(math.isfinite(v) for v in vectors[0])
        print("OK")
        """
    )
    _ok(result)


@pytest.mark.parametrize(
    "repo", ["BAAI/bge-reranker-v2-m3", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"]
)
def test_reranker_answers(run_offline, repo: str) -> None:  # type: ignore[no-untyped-def]
    model = MODELS["huggingface"][repo]
    result = run_offline(
        f"""
        from sentence_transformers import CrossEncoder
        model = CrossEncoder({repo!r}, revision={model["revision"]!r})
        good, bad = model.predict([
            ("What is the fee?", "The agreed fee is 48,500 EUR."),
            ("What is the fee?", "The meeting is on Tuesday."),
        ])
        assert good > bad, (good, bad)
        print("OK")
        """
    )
    _ok(result)


def test_hhem_answers(run_offline) -> None:  # type: ignore[no-untyped-def]
    model = MODELS["huggingface"]["vectara/hallucination_evaluation_model"]
    result = run_offline(
        f"""
        from transformers import AutoModelForSequenceClassification
        model = AutoModelForSequenceClassification.from_pretrained(
            "vectara/hallucination_evaluation_model",
            revision={model["revision"]!r},
            trust_remote_code=True,
        )
        supported, unsupported = model.predict([
            ("The agreed fee is 48,500 EUR.", "The fee is 48,500 EUR."),
            ("The agreed fee is 48,500 EUR.", "The fee is 90,000 EUR."),
        ]).tolist()
        assert supported > 0.5 > unsupported, (supported, unsupported)
        print("OK")
        """
    )
    _ok(result)


def test_ragas_tokenizer_offline(run_offline) -> None:  # type: ignore[no-untyped-def]
    result = run_offline(
        """
        import tiktoken
        for name in ("o200k_base", "cl100k_base"):
            assert tiktoken.get_encoding(name).encode("hola")
        print("OK")
        """
    )
    _ok(result)
