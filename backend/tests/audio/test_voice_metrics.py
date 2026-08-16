"""The evaluation's scoring is pure and lives in evaluation/voice/metrics.py
so it can be tested here without jiwer, the voice extra, or any audio."""

import pytest

from evaluation.voice.metrics import (
    ClipScore,
    aggregate,
    identifier_recall,
    normalize,
    tokens,
    word_error_rate,
)


def test_normalization_preserves_identifier_separators() -> None:
    # jiwer's default would split these into several tokens each; that's
    # exactly the measurement this module exists to protect.
    assert tokens("The worker calls run_layer_b in app/worker/pipeline.py, via tree-sitter.") == [
        "the", "worker", "calls", "run_layer_b", "in", "app/worker/pipeline.py", "via", "tree-sitter",
    ]


def test_spoken_form_aliases_apply_symmetrically() -> None:
    assert normalize("run underscore layer underscore b") == "run_layer_b"
    assert normalize("pipeline dot py") == "pipeline.py"
    assert normalize("app slash worker") == "app/worker"
    # Applied to the reference too, so neither side is favoured for saying
    # the word vs. emitting the symbol.
    assert word_error_rate("run underscore layer underscore b", "run_layer_b") == 0.0


def test_edge_punctuation_is_stripped_but_inner_kept() -> None:
    assert tokens("(asyncpg).") == ["asyncpg"]
    assert tokens("...run_layer_b...") == ["run_layer_b"]


def test_word_error_rate_is_edit_distance_over_reference_length() -> None:
    assert word_error_rate("a b c d", "a b c d") == 0.0
    assert word_error_rate("a b c d", "a x c d") == pytest.approx(0.25)
    assert word_error_rate("a b c d", "a b c") == pytest.approx(0.25)
    assert word_error_rate("a b c d", "a b c d e") == pytest.approx(0.25)
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "x") == 1.0
    # The identifier case: one mangled token is one error, not three.
    assert word_error_rate("calls run_layer_b now", "calls run layer b now") == pytest.approx(3 / 3)
    assert word_error_rate("calls run_layer_b now", "calls runlayerb now") == pytest.approx(1 / 3)


def test_identifier_recall_is_exact_token_presence() -> None:
    assert identifier_recall(["asyncpg", "arq"], "the api uses asyncpg and arq") == 1.0
    assert identifier_recall(["asyncpg", "arq"], "the api uses async PG and arc") == 0.0
    assert identifier_recall(["run_layer_b"], "it calls Run_Layer_B") == 1.0
    assert identifier_recall(["run_layer_b"], "it calls run underscore layer underscore b") == 1.0
    # A prose clip has nothing to find — None, not a misleading 100%.
    assert identifier_recall([], "anything") is None


def _score(backend: str, hinted: bool, category: str, wer: float, recall: float | None, ms: int, cost: float | None) -> ClipScore:
    return ClipScore(
        clip_id="c", category=category, backend=backend, hinted=hinted, reference="r", hypothesis="h",
        wer=wer, identifier_recall=recall, latency_ms=ms, estimated_cost_usd=cost, audio_seconds=1.0,
    )


def test_aggregate_groups_by_backend_hints_and_category_with_an_all_row() -> None:
    scores = [
        _score("local", False, "identifier", 0.5, 0.0, 100, None),
        _score("local", False, "prose", 0.0, None, 300, None),
        _score("local", True, "identifier", 0.25, 1.0, 120, None),
        _score("openai", False, "identifier", 0.1, 1.0, 900, 0.001),
    ]
    aggs = {(a.backend, a.hinted, a.category): a for a in aggregate(scores)}

    local_all = aggs[("local", False, "all")]
    assert local_all.clips == 2
    assert local_all.mean_wer == pytest.approx(0.25)
    # Recall averages only over clips that had identifiers.
    assert local_all.mean_identifier_recall == 0.0
    assert local_all.p50_latency_ms in (100, 300)
    assert local_all.total_cost_usd is None  # free

    assert aggs[("local", True, "identifier")].mean_identifier_recall == 1.0
    assert aggs[("openai", False, "all")].total_cost_usd == pytest.approx(0.001)
    # "all" rows sort before category rows within a (backend, hinted) group.
    order = [(a.backend, a.hinted, a.category) for a in aggregate(scores)]
    assert order.index(("local", False, "all")) < order.index(("local", False, "identifier"))
