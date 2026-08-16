import json

from app.analysis.subsystems import ROOT_KEY, UNREACHABLE, SubsystemPartition
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from app.semantics.subsystem_namer import (
    MAX_FILES_PER_SUBSYSTEM_IN_PROMPT,
    SubsystemNameItem,
    SubsystemNameOutput,
    name_subsystems,
)


def _partition(key: str, file_paths: tuple[str, ...], depth: int = 0) -> SubsystemPartition:
    return SubsystemPartition(key=key, file_paths=file_paths, depth=depth)


def _llm(items: list[SubsystemNameItem]) -> FakeLLMProvider:
    return FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=SubsystemNameOutput(subsystems=items),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            )
        ]
    )


async def test_no_partitions_makes_no_llm_call() -> None:
    llm = _llm([])
    assert await name_subsystems(llm, [], {}) == []
    assert llm.calls == []


async def test_names_and_roles_are_applied_by_key() -> None:
    partitions = [_partition("app/api", ("app/api/a.py",)), _partition("app/worker", ("app/worker/b.py",), depth=1)]
    llm = _llm(
        [
            SubsystemNameItem(key="app/worker", name="Background worker", role="Runs indexing jobs"),
            SubsystemNameItem(key="app/api", name="HTTP API", role="Serves requests"),
        ]
    )

    named = await name_subsystems(llm, partitions, {})

    assert [(n.key, n.name, n.role) for n in named] == [
        ("app/api", "HTTP API", "Serves requests"),
        ("app/worker", "Background worker", "Runs indexing jobs"),
    ]


async def test_order_follows_the_partition_not_the_response() -> None:
    # The partition's outside-in ordering is Layer A ground truth; the model
    # returning them in some other order must not reorder the study guide.
    partitions = [_partition("app/entry", ("a.py",)), _partition("app/leaf", ("b.py",), depth=3)]
    llm = _llm(
        [
            SubsystemNameItem(key="app/leaf", name="Leaf", role="r"),
            SubsystemNameItem(key="app/entry", name="Entry", role="r"),
        ]
    )

    named = await name_subsystems(llm, partitions, {})

    assert [n.order for n in named] == [0, 1]
    assert [n.key for n in named] == ["app/entry", "app/leaf"]


async def test_missing_name_falls_back_to_the_directory_segment() -> None:
    # A partition is a real thing whether or not the model named it; dropping
    # it would silently remove its files from the study guide.
    partitions = [_partition("app/semantics", ("app/semantics/a.py",))]
    llm = _llm([])

    named = await name_subsystems(llm, partitions, {})

    assert [(n.key, n.name) for n in named] == [("app/semantics", "Semantics")]


async def test_blank_name_falls_back_too() -> None:
    partitions = [_partition("app/module_summaries", ("a.py",))]
    llm = _llm([SubsystemNameItem(key="app/module_summaries", name="   ", role="r")])

    named = await name_subsystems(llm, partitions, {})

    assert named[0].name == "Module Summaries"


async def test_root_key_gets_a_readable_fallback() -> None:
    llm = _llm([])
    named = await name_subsystems(llm, [_partition(ROOT_KEY, ("setup.py",))], {})
    assert named[0].name == "Project root"


async def test_invented_subsystems_are_ignored() -> None:
    # The model can name the grouping but never change it (ADR-006) — a key it
    # wasn't given can't add a subsystem to the persisted set.
    partitions = [_partition("app/api", ("a.py",))]
    llm = _llm(
        [
            SubsystemNameItem(key="app/api", name="HTTP API", role="r"),
            SubsystemNameItem(key="app/invented", name="Invented", role="r"),
        ]
    )

    named = await name_subsystems(llm, partitions, {})

    assert [n.key for n in named] == ["app/api"]


async def test_membership_and_depth_come_from_the_partition() -> None:
    partition = _partition("app/api", ("app/api/a.py", "app/api/b.py"), depth=UNREACHABLE)
    llm = _llm([SubsystemNameItem(key="app/api", name="HTTP API", role="r")])

    named = await name_subsystems(llm, [partition], {})

    assert named[0].file_paths == ("app/api/a.py", "app/api/b.py")
    assert named[0].depth == UNREACHABLE


async def test_prompt_includes_module_purposes() -> None:
    partitions = [_partition("app/api", ("app/api/a.py",))]
    llm = _llm([SubsystemNameItem(key="app/api", name="HTTP API", role="r")])

    await name_subsystems(llm, partitions, {"app/api/a.py": "Serves the repos endpoint"})

    sent = llm.calls[0].messages[0].content
    assert "Serves the repos endpoint" in sent


async def test_prompt_bounds_files_per_subsystem() -> None:
    # MAX_SUBSYSTEMS bounds the partition, but a single subsystem is unbounded —
    # one directory holding thousands of files would otherwise send every path.
    file_paths = tuple(f"app/api/f{i:04d}.py" for i in range(MAX_FILES_PER_SUBSYSTEM_IN_PROMPT + 25))
    partitions = [_partition("app/api", file_paths)]
    llm = _llm([SubsystemNameItem(key="app/api", name="HTTP API", role="r")])

    named = await name_subsystems(llm, partitions, {})

    payload = json.loads(llm.calls[0].messages[0].content.split("Subsystems:\n", 1)[1])
    assert len(payload[0]["files"]) == MAX_FILES_PER_SUBSYSTEM_IN_PROMPT
    assert payload[0]["files_not_shown"] == 25
    # bounding the prompt must not bound what gets persisted
    assert named[0].file_paths == file_paths


async def test_one_call_for_the_whole_partition() -> None:
    # Names are only useful relative to each other — a model naming one
    # subsystem in isolation can't avoid overlapping with its siblings.
    partitions = [_partition(f"app/p{i}", (f"app/p{i}/a.py",)) for i in range(5)]
    llm = _llm([SubsystemNameItem(key=f"app/p{i}", name=f"P{i}", role="r") for i in range(5)])

    await name_subsystems(llm, partitions, {})

    assert len(llm.calls) == 1


async def test_truncated_response_falls_back_to_path_derived_names() -> None:
    # The subsystem *set* is deterministic — partitioning produced it, and only
    # the display names came from the model. A truncated response must not fail
    # the run and take the structure (and everything keyed off it) with it.
    partitions = [_partition("app/semantics", ("app/semantics/a.py",)), _partition("app/api", ("app/api/b.py",))]
    llm = FakeLLMProvider(
        [LLMResponse(text="", parsed=None, model="fake-model", stop_reason="max_tokens", usage={"output_tokens": 8192})]
    )

    named = await name_subsystems(llm, partitions, {})

    assert [n.key for n in named] == ["app/semantics", "app/api"]
    assert [n.name for n in named] == ["Semantics", "Api"]
    assert all(n.role == "" for n in named)
    assert [n.file_paths for n in named] == [("app/semantics/a.py",), ("app/api/b.py",)]
