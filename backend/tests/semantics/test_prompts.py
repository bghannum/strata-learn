from app.config import settings
from app.semantics.prompts import load_prompt


def test_load_prompt_module_summarizer() -> None:
    template = load_prompt("module_summarizer")
    assert template.name == "module_summarizer"
    assert template.version == "v1"
    assert "analyzing a single code module" in template.system
    assert template.render_input(file_path="a.py", import_list=["b"], code_units_json="[]") == (
        "File: a.py\nImports: ['b']\nStructure:\n[]"
    )


def test_load_prompt_pattern_detector() -> None:
    template = load_prompt("pattern_detector")
    assert "architectural pattern" in template.system
    rendered = template.render_input(dependency_graph_json="{}", directory_tree="a.py", entry_points_json="[]")
    assert "Dependency graph: {}" in rendered


def test_load_prompt_tradeoff_extractor() -> None:
    template = load_prompt("tradeoff_extractor")
    assert "WHY a specific technical decision" in template.system
    rendered = template.render_input(decision_description="d", code_snippet="c", context_snippet="ctx")
    assert "Decision point: d" in rendered


def test_load_prompt_subsystem_namer() -> None:
    template = load_prompt("subsystem_namer")
    assert "naming the parts of a codebase" in template.system
    rendered = template.render_input(subsystems_json="[]")
    assert "Subsystems:\n[]" in rendered


def test_load_prompt_architecture_narrative() -> None:
    template = load_prompt("architecture_narrative")
    assert "explaining how a codebase works" in template.system
    rendered = template.render_input(
        pattern_summary="modular monolith", subsystems_json="[]", entry_points_json="[]", tradeoffs_json="[]"
    )
    assert "Detected pattern: modular monolith" in rendered


def test_load_prompt_uses_overridden_prompts_dir(tmp_path, monkeypatch) -> None:
    # Regression test for the local-dev-vs-Docker path fix: load_prompt() must
    # read from settings.prompts_dir at call time, not a hardcoded relative
    # path, so it works under both the default (repo checkout) and an
    # overridden (container bind-mount) location.
    custom_dir = tmp_path / "prompts"
    custom_dir.mkdir()
    (custom_dir / "custom_prompt.v1.md").write_text(
        "# custom_prompt — v1\n\n## System\n\n```\nCustom system text.\n```\n\n## Input template\n\n```\nCustom: {value}\n```\n"
    )
    monkeypatch.setattr(settings, "prompts_dir", custom_dir)

    template = load_prompt("custom_prompt")
    assert template.system == "Custom system text."
    assert template.render_input(value="x") == "Custom: x"
