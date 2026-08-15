"""Implements docs/prompts/mcq_generator.v1.md (docs/design/original-project-plan.md §9.4):
one LLM call per QuestionSeed (an already-persisted, already-cited Citation —
see generation.py), no re-read of the source repo needed.

correct_index is validated against len(choices) before a result is kept
(Ground Rule #3's "don't persist an unverifiable claim" spirit, same as
tradeoff_extractor.py's _valid_ref) — an out-of-range index would otherwise
make the question ungradable (mcq_grader.py's equality check would never be
satisfiable if we don't check this now, or would grade a bogus answer as
"correct" if paired with a coincidentally-matching one).
"""

from dataclasses import dataclass

from pydantic import BaseModel

from app.quizzing.seeds import QuestionSeed
from app.semantics.llm_provider import LLMProvider, Message
from app.semantics.prompts import load_prompt


class MCQOutput(BaseModel):
    prompt: str
    choices: list[str]
    correct_index: int
    explanation: str


@dataclass(frozen=True)
class MCQResult:
    prompt: str
    choices: list[str]
    correct_index: int
    explanation: str
    seed: QuestionSeed
    prompt_version: str
    model: str


def _valid(output: MCQOutput) -> bool:
    return len(output.choices) >= 2 and 0 <= output.correct_index < len(output.choices)


async def generate_mcq_questions(llm: LLMProvider, seeds: list[QuestionSeed]) -> list[MCQResult]:
    # v2 asks for conceptual questions rather than recall — see the prompt's own
    # "Changes from v1" section. The persisted prompt_version on each Question
    # records which one produced it, so a quiz generated before this still
    # reports v1.
    template = load_prompt("mcq_generator", "v2")
    results: list[MCQResult] = []

    for seed in seeds:
        input_text = template.render_input(source_fact=seed.claim_excerpt, code_snippet=seed.snippet_text)
        response = await llm.complete(
            system=template.system,
            messages=[Message(role="user", content=input_text)],
            response_schema=MCQOutput,
        )
        output = response.parsed
        assert isinstance(output, MCQOutput)

        if not _valid(output):
            # Drop, don't fix up — a model that got the shape of its own
            # answer key wrong isn't a reliable source for the rest of the
            # question either (same "don't persist unverified" call as
            # tradeoff_extractor.py's dropped-card case).
            continue

        results.append(
            MCQResult(
                prompt=output.prompt,
                choices=output.choices,
                correct_index=output.correct_index,
                explanation=output.explanation,
                seed=seed,
                prompt_version=template.version,
                model=response.model,
            )
        )

    return results
