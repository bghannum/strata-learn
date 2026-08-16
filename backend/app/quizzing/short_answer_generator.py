"""Implements docs/prompts/short_answer_generator.v1.md: one LLM call per
QuestionSeed, same sourcing and shape as mcq_generator.py.

A result is dropped (not fixed up) if the rubric is outside 2–4 points, any
point is blank, or the question or model answer is empty — a question with a
degenerate rubric can't be graded honestly (score = hits/total needs a real
denominator), same "don't persist unverified" call as mcq_generator.py's
correct_index check.
"""

import logging
from dataclasses import dataclass

from pydantic import BaseModel

from app.quizzing.seeds import QuestionSeed
from app.semantics.llm_provider import (
    LLMOutputError,
    LLMProvider,
    Message,
    require_parsed,
)
from app.semantics.prompts import load_prompt

logger = logging.getLogger(__name__)

MIN_RUBRIC_POINTS = 2
MAX_RUBRIC_POINTS = 4


class ShortAnswerOutput(BaseModel):
    prompt: str
    model_answer: str
    rubric: list[str]


@dataclass(frozen=True)
class ShortAnswerResult:
    prompt: str
    model_answer: str
    rubric: list[str]
    seed: QuestionSeed
    prompt_version: str
    model: str


def _valid(output: ShortAnswerOutput) -> bool:
    points = [p.strip() for p in output.rubric]
    return (
        bool(output.prompt.strip())
        and bool(output.model_answer.strip())
        and MIN_RUBRIC_POINTS <= len(points) <= MAX_RUBRIC_POINTS
        and all(points)
    )


async def generate_short_answer_questions(llm: LLMProvider, seeds: list[QuestionSeed]) -> list[ShortAnswerResult]:
    template = load_prompt("short_answer_generator", "v1")
    results: list[ShortAnswerResult] = []

    for seed in seeds:
        input_text = template.render_input(source_fact=seed.claim_excerpt, code_snippet=seed.snippet_text)
        response = await llm.complete(
            system=template.system,
            messages=[Message(role="user", content=input_text)],
            response_schema=ShortAnswerOutput,
        )
        try:
            output = require_parsed(response, ShortAnswerOutput)
        except LLMOutputError as exc:
            logger.warning("Skipping short-answer for seed %s: %s", seed.claim_excerpt[:60], exc)
            continue

        if not _valid(output):
            continue

        results.append(
            ShortAnswerResult(
                prompt=output.prompt.strip(),
                model_answer=output.model_answer.strip(),
                rubric=[p.strip() for p in output.rubric],
                seed=seed,
                prompt_version=template.version,
                model=response.model,
            )
        )

    return results
