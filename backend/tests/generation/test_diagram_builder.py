from app.generation.diagram_builder import DiagramLabelItem, DiagramLabelOutput, build_component_diagram
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse


def _graph(nodes: list[dict], edges: list[dict]) -> dict:
    return {"nodes": nodes, "edges": edges}


def _label_response(labels: dict[str, str]) -> LLMResponse:
    return LLMResponse(
        text="",
        parsed=DiagramLabelOutput(labels=[DiagramLabelItem(file_path=p, label=label) for p, label in labels.items()]),
        model="fake-model",
        stop_reason="end_turn",
        usage={},
    )


async def test_build_component_diagram_returns_none_for_isolated_file() -> None:
    # A single file with no internal edges isn't a "component diagram" of
    # anything — see diagram_builder._select_nodes's own comment.
    graph = _graph([{"id": "app.py", "kind": "file"}], [])
    result = await build_component_diagram(FakeLLMProvider([]), graph, {})
    assert result is None


async def test_build_component_diagram_renders_nodes_and_edges() -> None:
    graph = _graph(
        [{"id": "app/main.py", "kind": "file"}, {"id": "app/config.py", "kind": "file"}],
        [{"source": "app/main.py", "target": "app/config.py", "kind": "imports"}],
    )
    llm = FakeLLMProvider([_label_response({"app/config.py": "Settings", "app/main.py": "App Entry Point"})])

    result = await build_component_diagram(llm, graph, {})

    assert result is not None
    assert result.file_paths == ["app/config.py", "app/main.py"]
    assert result.mermaid.startswith("flowchart TD\n")
    assert '["Settings"]' in result.mermaid
    assert '["App Entry Point"]' in result.mermaid
    assert "-->" in result.mermaid
    assert result.prompt_version == "v1"
    assert result.model == "fake-model"
    assert result.labels == {"app/config.py": "Settings", "app/main.py": "App Entry Point"}


async def test_build_component_diagram_excludes_external_packages() -> None:
    # External dependency nodes aren't "components" of this repo's own
    # structure — see _select_nodes's docstring.
    graph = _graph(
        [{"id": "app/main.py", "kind": "file"}, {"id": "external:fastapi", "kind": "external"}],
        [{"source": "app/main.py", "target": "external:fastapi", "kind": "imports_external"}],
    )
    result = await build_component_diagram(FakeLLMProvider([]), graph, {})
    assert result is None  # app/main.py has no *internal* edges


async def test_build_component_diagram_falls_back_to_basename_when_unlabeled() -> None:
    graph = _graph(
        [{"id": "app/main.py", "kind": "file"}, {"id": "app/db_utils.py", "kind": "file"}],
        [{"source": "app/main.py", "target": "app/db_utils.py", "kind": "imports"}],
    )
    # LLM only labels one of the two files — the other must still render with
    # a deterministic fallback, not a blank or missing node.
    llm = FakeLLMProvider([_label_response({"app/main.py": "App Entry Point"})])

    result = await build_component_diagram(llm, graph, {})

    assert result is not None
    assert '["App Entry Point"]' in result.mermaid
    assert '["Db Utils"]' in result.mermaid
    assert result.labels["app/db_utils.py"] == "Db Utils"  # fallback label, resolved into .labels too


async def test_build_component_diagram_sanitizes_unsafe_label_characters() -> None:
    graph = _graph(
        [{"id": "app/main.py", "kind": "file"}, {"id": "app/config.py", "kind": "file"}],
        [{"source": "app/main.py", "target": "app/config.py", "kind": "imports"}],
    )
    llm = FakeLLMProvider([_label_response({"app/main.py": 'App "Entry" [Point]', "app/config.py": "Settings"})])

    result = await build_component_diagram(llm, graph, {})

    assert result is not None
    # The raw label had embedded quotes/brackets — sanitized down to a plain
    # phrase so it can't break out of Mermaid's `id["label"]` node syntax.
    assert '["App Entry Point"]' in result.mermaid
    main_line = next(line for line in result.mermaid.splitlines() if "Entry" in line)
    assert main_line.count('"') == 2


async def test_build_component_diagram_strips_embedded_newlines_from_labels() -> None:
    # A structured LLM response isn't prevented from containing an embedded
    # newline — left intact, it splits a Mermaid node declaration across
    # lines, corrupting the diagram's syntax.
    graph = _graph(
        [{"id": "app/main.py", "kind": "file"}, {"id": "app/config.py", "kind": "file"}],
        [{"source": "app/main.py", "target": "app/config.py", "kind": "imports"}],
    )
    llm = FakeLLMProvider([_label_response({"app/main.py": "App\nEntry\r\nPoint", "app/config.py": "Settings"})])

    result = await build_component_diagram(llm, graph, {})

    assert result is not None
    assert '["App Entry Point"]' in result.mermaid
    lines = result.mermaid.splitlines()
    assert len(lines) == 1 + 2 + 1  # header + 2 nodes + 1 edge, no extra lines from a split label


async def test_build_component_diagram_falls_back_to_path_labels_when_truncated() -> None:
    # Nodes and edges are derived from the dependency graph; only the labels
    # came from the model. A truncated response should cost the nicer names,
    # not the whole diagram.
    graph = _graph(
        [{"id": "app/main.py", "kind": "file"}, {"id": "app/config.py", "kind": "file"}],
        [{"source": "app/main.py", "target": "app/config.py", "kind": "imports"}],
    )
    llm = FakeLLMProvider(
        [LLMResponse(text="", parsed=None, model="fake-model", stop_reason="max_tokens", usage={"output_tokens": 8192})]
    )

    result = await build_component_diagram(llm, graph, {})

    assert result is not None
    assert "Main" in result.mermaid  # _fallback_label("app/main.py")
    assert "Config" in result.mermaid
    assert "-->" in result.mermaid  # the edge still renders
