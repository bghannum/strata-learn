from app.audio.vocabulary import build_vocabulary, build_vocabulary_prompt

SNIPPET = """
async def run_layer_b(llm: LLMProvider, snapshot: AnalysisSnapshot, source_dir: Path) -> None:
    summaries = await summarize_modules(llm, snapshot, source_dir)
    await name_subsystems(llm, snapshot, summaries)
    return None
"""


def _build(**overrides):
    kwargs = {
        "snippet_text": SNIPPET,
        "file_path": "app/semantics/orchestrator.py",
        "subsystem_name": "Semantic analysis",
        "excluded_terms": [],
        "max_terms": 24,
        "max_chars": 300,
    }
    kwargs.update(overrides)
    return build_vocabulary(**kwargs)


def test_picks_identifiers_paths_and_the_subsystem_name_over_english() -> None:
    terms = _build()
    assert "run_layer_b" in terms
    assert "summarize_modules" in terms
    assert "AnalysisSnapshot" in terms
    assert "orchestrator.py" in terms
    assert "semantics" in terms
    assert "Semantic analysis" in terms
    # Ordinary lowercase words and keywords never make the list — the model
    # already knows them, and they'd crowd out what it doesn't.
    assert "async" not in terms
    assert "return" not in terms
    assert "None" not in terms


def test_the_answer_key_is_never_a_hint() -> None:
    # The load-bearing property of this module. An ASR prompt biases decoding
    # toward its terms; for a code-mode fill-blank graded by exact match,
    # priming the correct token turns a mumbled approximation into the
    # answer. So the key is excluded by construction, and it stays excluded
    # even when it appears in the snippet the hints are drawn from.
    terms = _build(excluded_terms=["run_layer_b", "summarize_modules"])
    assert "run_layer_b" not in terms
    assert "summarize_modules" not in terms
    # ...while the surrounding identifiers still help.
    assert "name_subsystems" in terms


def test_exclusion_is_case_insensitive_and_covers_alternatives() -> None:
    terms = _build(excluded_terms=["RUN_LAYER_B", " name_subsystems "])
    assert "run_layer_b" not in terms
    assert "name_subsystems" not in terms


def test_exclusion_catches_terms_the_answer_is_a_component_of() -> None:
    # With "orchestrator" as the answer, priming "orchestrator.py" still
    # primes the answer. Over-excluding costs a hint; under-excluding leaks
    # the key, so this errs strict.
    terms = _build(excluded_terms=["orchestrator"])
    assert "orchestrator.py" not in terms
    terms = _build(excluded_terms=["layer"])
    assert "run_layer_b" not in terms


def test_bounded_by_term_count() -> None:
    terms = _build(max_terms=3)
    assert len(terms) == 3


def test_bounded_by_joined_length_which_wins_over_term_count() -> None:
    # The character cap is the one that bounds the paid request.
    terms = _build(max_terms=24, max_chars=40)
    assert len(", ".join(terms)) <= 40
    assert len(terms) < 24


def test_generic_path_segments_carry_no_signal() -> None:
    terms = _build(snippet_text="", subsystem_name=None, file_path="app/src/backend/quizzing/mastery.py")
    assert "mastery.py" in terms
    assert "quizzing" in terms
    assert "app" not in terms
    assert "src" not in terms
    assert "backend" not in terms


def test_empty_inputs_produce_no_hints() -> None:
    assert _build(snippet_text=None, file_path=None, subsystem_name=None) == []


def test_prompt_format_is_shared_and_stable() -> None:
    # Both backends condition on this exact string, so an evaluation
    # comparing them measures the model and not the prompt encoding.
    assert build_vocabulary_prompt([]) == ""
    assert build_vocabulary_prompt(["asyncpg", "run_layer_b"]) == "Technical terms: asyncpg, run_layer_b."
