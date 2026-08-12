"""Loads versioned prompt templates from docs/prompts/*.v{version}.md
(PROJECT_PLAN.md §9.0: prompts live as files, not inline strings, so they can
be iterated on independently of code)."""

import re
from dataclasses import dataclass

from app.config import settings

_SYSTEM_BLOCK = re.compile(r"## System\n\n```\n(.*?)\n```", re.DOTALL)
_INPUT_TEMPLATE_BLOCK = re.compile(r"## Input template\n\n```\n(.*?)\n```", re.DOTALL)


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    system: str
    input_template: str

    def render_input(self, **kwargs: object) -> str:
        return self.input_template.format(**kwargs)


def load_prompt(name: str, version: str = "v1") -> PromptTemplate:
    path = settings.prompts_dir / f"{name}.{version}.md"
    text = path.read_text()

    system_match = _SYSTEM_BLOCK.search(text)
    if system_match is None:
        raise ValueError(f"{path}: no '## System' fenced code block found")

    input_match = _INPUT_TEMPLATE_BLOCK.search(text)
    if input_match is None:
        raise ValueError(f"{path}: no '## Input template' fenced code block found")

    return PromptTemplate(
        name=name,
        version=version,
        system=system_match.group(1),
        input_template=input_match.group(1),
    )
