"""Implements docs/prompts/fill_blank_generator.v1.md (docs/design/original-project-plan.md §9.5):
one LLM call per QuestionSeed, same sourcing as mcq_generator.py.

A result is dropped (not fixed up) if blanked_text doesn't actually contain
the "___" marker or correct_answer is empty — either would make the question
ungradable/unanswerable, same "don't persist unverified" call as
mcq_generator.py's correct_index check.
"""

import logging
from dataclasses import dataclass

from pydantic import BaseModel

from app.quizzing.seeds import QuestionSeed
from app.semantics.llm_provider import LLMOutputError, LLMProvider, Message, require_parsed
from app.semantics.prompts import load_prompt

logger = logging.getLogger(__name__)

BLANK_MARKER = "___"


class FillBlankOutput(BaseModel):
    mode: str
    blanked_text: str
    correct_answer: str
    acceptable_alternatives: list[str]


@dataclass(frozen=True)
class FillBlankResult:
    mode: str
    blanked_text: str
    correct_answer: str
    acceptable_alternatives: list[str]
    seed: QuestionSeed
    prompt_version: str
    model: str


def _valid(output: FillBlankOutput) -> bool:
    return (
        output.mode in ("code", "concept")
        and BLANK_MARKER in output.blanked_text
        and bool(output.correct_answer.strip())
    )


async def generate_fill_blank_questions(llm: LLMProvider, seeds: list[QuestionSeed]) -> list[FillBlankResult]:
    # v2 makes CONCEPT mode the default and requires CODE mode to earn its use
    # — see the prompt's own "Changes from v1" section.
    template = load_prompt("fill_blank_generator", "v2")
    results: list[FillBlankResult] = []

    for seed in seeds:
        input_text = template.render_input(source_fact=seed.claim_excerpt, code_snippet=seed.snippet_text)
        response = await llm.complete(
            system=template.system,
            messages=[Message(role="user", content=input_text)],
            response_schema=FillBlankOutput,
        )
        try:
            output = require_parsed(response, FillBlankOutput)
        except LLMOutputError as exc:
            logger.warning("Skipping fill-blank for seed %s: %s", seed.claim_excerpt[:60], exc)
            continue

        if not _valid(output):
            continue

        results.append(
            FillBlankResult(
                mode=output.mode,
                blanked_text=output.blanked_text,
                correct_answer=output.correct_answer,
                acceptable_alternatives=output.acceptable_alternatives,
                seed=seed,
                prompt_version=template.version,
                model=response.model,
            )
        )

    return results
