import uuid

from app.quizzing.seeds import QuestionSeed
from app.quizzing.short_answer_generator import (
    ShortAnswerOutput,
    generate_short_answer_questions,
)
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse


def _seed() -> QuestionSeed:
    return QuestionSeed(
        citation_id=uuid.uuid4(),
        claim_excerpt="indexing runs in a background job so the request returns immediately",
        snippet_text="await redis.enqueue_job('index_repo', ...)",
        file_path="app/api/repos.py", line_start=1, line_end=5, subsystem_key="app/api",
    )


def _response(output: ShortAnswerOutput) -> LLMResponse:
    return LLMResponse(text="", parsed=output, model="fake-model", stop_reason="end_turn", usage={})


async def test_builds_result_from_parsed_output_and_carries_the_seed() -> None:
    llm = FakeLLMProvider(
        [
            _response(
                ShortAnswerOutput(
                    prompt="Why does indexing run in a background job rather than in the HTTP request?",
                    model_answer="Cloning and parsing take seconds to minutes. A request would time out; the job persists status.",
                    rubric=["  the pipeline is slow ", "a request would time out", "status is persisted for polling"],
                )
            )
        ]
    )

    results = await generate_short_answer_questions(llm, [_seed()])

    assert len(results) == 1
    result = results[0]
    assert result.prompt.startswith("Why does indexing")
    # Rubric points are stripped; order preserved (the grader lines hits up
    # against it by position).
    assert result.rubric == ["the pipeline is slow", "a request would time out", "status is persisted for polling"]
    assert result.seed.subsystem_key == "app/api"
    assert result.prompt_version == "v1"
    assert result.model == "fake-model"
    # The prompt template actually rendered the seed into the call.
    sent = llm.calls[0].messages[0].content
    assert "background job" in sent and "enqueue_job" in sent


async def test_drops_results_with_a_degenerate_rubric() -> None:
    # A rubric outside 2–4 points, or with a blank point, can't be graded
    # honestly (score = hits/total needs a real denominator) — dropped, not
    # fixed up, same as mcq_generator's correct_index check.
    llm = FakeLLMProvider(
        [
            _response(ShortAnswerOutput(prompt="q", model_answer="a", rubric=["only one"])),
            _response(ShortAnswerOutput(prompt="q", model_answer="a", rubric=["1", "2", "3", "4", "5"])),
            _response(ShortAnswerOutput(prompt="q", model_answer="a", rubric=["fine", "   "])),
            _response(ShortAnswerOutput(prompt="  ", model_answer="a", rubric=["fine", "also fine"])),
            _response(ShortAnswerOutput(prompt="q", model_answer="a", rubric=["fine", "also fine"])),
        ]
    )
    results = await generate_short_answer_questions(llm, [_seed()] * 5)
    assert len(results) == 1
    assert results[0].rubric == ["fine", "also fine"]


async def test_skips_an_unparsable_response_and_continues() -> None:
    llm = FakeLLMProvider(
        [
            LLMResponse(text="", parsed=None, model="fake-model", stop_reason="max_tokens", usage={}),
            _response(ShortAnswerOutput(prompt="q", model_answer="a", rubric=["one", "two"])),
        ]
    )
    results = await generate_short_answer_questions(llm, [_seed(), _seed()])
    assert len(results) == 1
