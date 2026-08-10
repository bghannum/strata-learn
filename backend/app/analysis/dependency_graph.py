"""Builds the repo's import/dependency graph from parsed files. LAYER A —
edges are resolved deterministically from literal import statements, no LLM
involved (ADR-006); Phase 2/3 only ever *label* these edges, never invent them.

Two kinds of edge:
- "imports"          — resolved to another file in this repo
- "imports_external" — resolved to a third-party package, not a repo file

A relative/absolute import that can't be resolved to either (e.g. a typo, a
dynamic import pattern we don't parse) is dropped rather than guessed at.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath

from app.analysis.parser import ParsedFile, ParsedImport
from app.ingestion.language_detect import Language

_JS_RESOLVE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


@dataclass(frozen=True)
class FileInfo:
    relative_path: str
    language: Language


def build_dependency_graph(parsed_files: list[ParsedFile], files: list[FileInfo]) -> dict:
    language_by_path = {f.relative_path: f.language for f in files}
    file_set = set(language_by_path)
    python_module_map = _build_python_module_map(files)

    nodes: list[dict] = [
        {"id": path, "kind": "file", "language": language.value} for path, language in language_by_path.items()
    ]
    node_ids = {n["id"] for n in nodes}
    edges: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def add_edge(source: str, target: str, kind: str) -> None:
        key = (source, target, kind)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"source": source, "target": target, "kind": kind})

    def external_node_id(package: str) -> str:
        node_id = f"external:{package}"
        if node_id not in node_ids:
            node_ids.add(node_id)
            nodes.append({"id": node_id, "kind": "external", "language": None})
        return node_id

    for pf in parsed_files:
        for imp in pf.imports:
            if imp.kind == "python_absolute":
                # `from pkg import submodule` is grammatically identical to
                # `from pkg import some_attribute` — check whether any imported
                # name is itself a real submodule file before falling back to
                # a single edge at the base module (§ handles `import a.b.c` too,
                # since that path is always a module — never an attribute).
                submodules = _python_submodule_targets(imp.raw, imp.names, python_module_map)
                if submodules:
                    for target in submodules:
                        add_edge(pf.relative_path, target, "imports")
                else:
                    target = _resolve_python_absolute(imp.raw, python_module_map)
                    if target is not None:
                        add_edge(pf.relative_path, target, "imports")
                    else:
                        package = imp.raw.split(".")[0]
                        add_edge(pf.relative_path, external_node_id(package), "imports_external")
            elif imp.kind == "python_relative":
                base_dotted = _python_relative_full_base(imp, pf.relative_path)
                submodules = (
                    _python_submodule_targets(base_dotted, imp.names, python_module_map)
                    if base_dotted is not None
                    else []
                )
                if submodules:
                    for target in submodules:
                        add_edge(pf.relative_path, target, "imports")
                elif base_dotted is not None and base_dotted in python_module_map:
                    add_edge(pf.relative_path, python_module_map[base_dotted], "imports")
                # unresolved relative imports are dropped, not treated as external —
                # by construction they can only point within this repo
            elif imp.kind == "js_relative":
                target = _resolve_js_relative(imp.raw, pf.relative_path, file_set)
                if target is not None:
                    add_edge(pf.relative_path, target, "imports")
            elif imp.kind == "js_bare":
                package = _js_package_name(imp.raw)
                add_edge(pf.relative_path, external_node_id(package), "imports_external")

    return {"nodes": nodes, "edges": edges}


# --- Python resolution ---


def _build_python_module_map(files: list[FileInfo]) -> dict[str, str]:
    """Maps a fully-qualified dotted module name (as if the repo root were on
    sys.path) to the file that defines it. `pkg/__init__.py` maps under the
    package's own dotted name (`pkg`), not `pkg.__init__`."""
    module_map: dict[str, str] = {}
    for f in files:
        if f.language is not Language.python:
            continue
        parts = f.relative_path.rsplit(".", 1)[0].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
            if not parts:
                continue  # a root-level __init__.py — no dotted name to assign
        module_map[".".join(parts)] = f.relative_path
    return module_map


def _resolve_python_absolute(raw: str, module_map: dict[str, str]) -> str | None:
    """`raw` (the part of `import`/`from ... import` before the imported names)
    is always itself a real module/package path per Python import semantics —
    never an attribute — so the exact dotted string is tried first. The
    progressively-shorter fallback only guards against grammar edge cases;
    the common `from pkg import submodule` ambiguity is handled separately by
    `_python_submodule_targets`, which inspects the imported *names*."""
    segments = raw.split(".")
    for end in range(len(segments), 0, -1):
        candidate = ".".join(segments[:end])
        if candidate in module_map:
            return module_map[candidate]
    return None


def _python_submodule_targets(base_dotted: str, names: tuple[str, ...], module_map: dict[str, str]) -> list[str]:
    """For `from pkg import name`, `name` might be a submodule (`pkg/name.py`)
    rather than an attribute of `pkg` — this is indistinguishable from the
    import statement's grammar alone. Prefer the more specific submodule edge
    whenever one exists."""
    targets = []
    for name in names:
        candidate = f"{base_dotted}.{name}" if base_dotted else name
        if candidate in module_map:
            targets.append(module_map[candidate])
    return targets


def _python_relative_base_parts(imp: ParsedImport, importing_file: str) -> list[str] | None:
    """The dotted parts of the importing file's own package (`__package__` in
    Python's own terms) after applying the import's leading-dot level. This is
    the enclosing directory for a regular module *and* for `__init__.py`
    itself — both drop exactly one path segment to get there."""
    stem_parts = importing_file.rsplit(".", 1)[0].split("/")
    package_parts = stem_parts[:-1]

    # level=1 ("from . import x") means "current package" — zero extra hops up.
    hops = imp.level - 1
    if hops > len(package_parts):
        return None  # would climb above the repo root — unresolvable
    return package_parts[: len(package_parts) - hops] if hops > 0 else package_parts


def _python_relative_full_base(imp: ParsedImport, importing_file: str) -> str | None:
    """The dotted module path the `from` clause itself refers to — base package
    plus the relative import's own module component, e.g. base "app" + raw "db"
    for "from ..db import session" in app/api/quizzes.py -> "app.db". This is
    what `names` (the imported names) are checked against for the
    submodule-vs-attribute ambiguity, and the fallback target if none match."""
    base_parts = _python_relative_base_parts(imp, importing_file)
    if base_parts is None:
        return None
    has_submodule = imp.raw != "." * imp.level
    target_parts = [*base_parts, *imp.raw.split(".")] if has_submodule else base_parts
    return ".".join(target_parts)


# --- JS/TS resolution ---


def _resolve_js_relative(raw: str, importing_file: str, file_set: set[str]) -> str | None:
    importing_dir = PurePosixPath(importing_file).parent
    base = _normalize_posix(importing_dir / raw)

    if base.as_posix() in file_set:
        return base.as_posix()
    for ext in _JS_RESOLVE_EXTENSIONS:
        candidate = base.as_posix() + ext
        if candidate in file_set:
            return candidate
    for ext in _JS_RESOLVE_EXTENSIONS:
        candidate = (base / f"index{ext}").as_posix()
        if candidate in file_set:
            return candidate
    return None


def _normalize_posix(path: PurePosixPath) -> PurePosixPath:
    parts: list[str] = []
    for part in path.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part == ".":
            continue
        else:
            parts.append(part)
    return PurePosixPath(*parts) if parts else PurePosixPath(".")


def _js_package_name(raw: str) -> str:
    segments = raw.split("/")
    if raw.startswith("@") and len(segments) >= 2:
        return "/".join(segments[:2])
    return segments[0]
