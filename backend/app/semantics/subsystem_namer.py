"""Implements docs/prompts/subsystem_namer.v1.md: gives the deterministic
subsystem partition (analysis/subsystems.py) human names and one-line roles.

LAYER B, but a narrow one — the model labels a grouping it did not choose and
cannot change (ADR-006). Every subsystem gets a name whether or not the call
returns one for it, via the same deterministic-fallback shape diagram_builder.py
uses for node labels: a partition with a missing name is still a real partition,
and dropping it would silently remove files from the study guide.
"""

import json
from dataclasses import dataclass

from pydantic import BaseModel

from app.analysis.subsystems import ROOT_KEY, SubsystemPartition
from app.semantics.llm_provider import LLMProvider, Message
from app.semantics.prompts import load_prompt

# Per-subsystem cap on files named in the prompt. The partition itself is
# already bounded (MAX_SUBSYSTEMS), but a single subsystem is not — a repo with
# 3,000 files in one directory would otherwise put all 3,000 paths and purposes
# in one request. Naming a subsystem doesn't need every file, just enough of
# them to see what it is.
MAX_FILES_PER_SUBSYSTEM_IN_PROMPT = 40


class SubsystemNameItem(BaseModel):
    key: str
    name: str
    role: str


class SubsystemNameOutput(BaseModel):
    subsystems: list[SubsystemNameItem]


@dataclass(frozen=True)
class NamedSubsystem:
    key: str
    name: str
    role: str
    file_paths: tuple[str, ...]
    depth: int
    order: int
    prompt_version: str
    model: str


def _fallback_name(key: str) -> str:
    """Last directory segment, humanized — "app/semantics" becomes "Semantics".
    Same idea as diagram_builder's _fallback_label: never leave a real thing
    unnamed just because one field of one LLM response was missing."""
    if key == ROOT_KEY:
        return "Project root"
    segment = key.rsplit("/", 1)[-1]
    return segment.replace("_", " ").replace("-", " ").strip().title() or "Unnamed"


def _prompt_payload(partition: SubsystemPartition, module_purposes: dict[str, str]) -> dict:
    shown = partition.file_paths[:MAX_FILES_PER_SUBSYSTEM_IN_PROMPT]
    payload = {
        "key": partition.key,
        "files": [{"file_path": p, "purpose": module_purposes.get(p)} for p in shown],
    }
    if len(partition.file_paths) > len(shown):
        payload["files_not_shown"] = len(partition.file_paths) - len(shown)
    return payload


async def name_subsystems(
    llm: LLMProvider, partitions: list[SubsystemPartition], module_purposes: dict[str, str]
) -> list[NamedSubsystem]:
    if not partitions:
        return []

    template = load_prompt("subsystem_namer")
    input_text = template.render_input(
        subsystems_json=json.dumps([_prompt_payload(p, module_purposes) for p in partitions])
    )
    response = await llm.complete(
        system=template.system,
        messages=[Message(role="user", content=input_text)],
        response_schema=SubsystemNameOutput,
    )
    output = response.parsed
    assert isinstance(output, SubsystemNameOutput)

    # Keyed by the partition's own key, and only for keys that were actually
    # sent — a model that invents a subsystem, merges two, or renames a key
    # can't add or remove anything from the persisted set.
    valid_keys = {p.key for p in partitions}
    named = {item.key: item for item in output.subsystems if item.key in valid_keys}

    return [
        NamedSubsystem(
            key=p.key,
            name=(named[p.key].name.strip() if p.key in named and named[p.key].name.strip() else _fallback_name(p.key)),
            role=(named[p.key].role.strip() if p.key in named else ""),
            file_paths=p.file_paths,
            depth=p.depth,
            # Position in the partition's own outside-in order, persisted so
            # consumers don't each re-derive it (and can't disagree about it).
            order=order,
            prompt_version=template.version,
            model=response.model,
        )
        for order, p in enumerate(partitions)
    ]
