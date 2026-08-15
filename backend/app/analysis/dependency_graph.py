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


def build_dependency_graph(
    parsed_files: list[ParsedFile], files: list[FileInfo], package_roots: set[str] | None = None
) -> dict:
    language_by_path = {f.relative_path: f.language for f in files}
    file_set = set(language_by_path)
    python_module_map = _build_python_module_map(files, package_roots or {""})

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


def _dotted_name(relative_path: str, root_prefix: str) -> str | None:
    """Dotted module name for a file, as seen from `root_prefix` on sys.path.
    `pkg/__init__.py` maps under the package's own dotted name (`pkg`), not
    `pkg.__init__`."""
    stem = relative_path.rsplit(".", 1)[0]
    if root_prefix:
        stem = stem[len(root_prefix) + 1 :]  # +1 drops the separator
    parts = stem.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
        if not parts:
            return None  # an __init__.py at the root itself — no dotted name to assign
    return ".".join(parts)


# A directory holding one of these is a Python project root, so its own
# directory — not the repo root — is what lands on sys.path.
_PYTHON_PROJECT_MARKERS = frozenset({"pyproject.toml", "setup.py", "setup.cfg"})


def detect_python_package_roots(relative_paths: list[str]) -> set[str]:
    """Directory prefixes that could be on sys.path, "" meaning the repo root.

    Resolving absolute imports against the repo root alone (#57) misses every
    import in a repo whose Python doesn't live at the top: a monorepo with
    `backend/pyproject.toml` writes `from app.config import settings`, not
    `from backend.app.config import ...`, so nothing resolved and the entire
    backend appeared to have no internal dependencies at all.

    Three signals, because no single one covers real repos:

    - A project marker file (`pyproject.toml` and friends) marks its own
      directory. This is the one that catches namespace-package projects, which
      have no `__init__.py` anywhere — including this repo, which is why the
      `__init__.py` walk alone found nothing when it was tried first.
    - `src/` beside a marker, for the src-layout convention.
    - The parent of a topmost `__init__.py` chain, for projects that do use
      them and have no marker file (a plain directory of packages).
    """
    roots = {""}
    directories = set()
    package_dirs = set()

    for path in relative_paths:
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        directories.add(directory)
        name = path.rsplit("/", 1)[-1]
        if name in _PYTHON_PROJECT_MARKERS:
            roots.add(directory)
        elif name == "__init__.py":
            package_dirs.add(directory)

    # src-layout: `<project>/src/<package>/...` with the marker at <project>.
    for marker_root in list(roots):
        src = f"{marker_root}/src" if marker_root else "src"
        if src in directories or any(d.startswith(f"{src}/") for d in directories):
            roots.add(src)

    # Topmost package's parent, for repos that use __init__.py and nothing else.
    for package_dir in package_dirs:
        parts = package_dir.split("/")
        while parts and "/".join(parts) in package_dirs:
            parts.pop()
        roots.add("/".join(parts))

    return roots


def _build_python_module_map(files: list[FileInfo], package_roots: set[str]) -> dict[str, str]:
    """Maps a dotted module name to the file that defines it.

    A file is registered once per package root it sits under: `app.config` from
    `backend/`, and `backend.app.config` from the repo root. Both are
    legitimate names for the same module depending on what is on sys.path, and
    a repo can genuinely contain imports written either way — its own code says
    `app.config` while a top-level script might say `backend.app.config`.
    """
    python_files = sorted(f.relative_path for f in files if f.language is Language.python)

    module_map: dict[str, str] = {}

    # Repo-root names first, so pre-existing resolution always wins a collision
    # and this can only add edges, never redirect one that already resolved.
    for relative_path in python_files:
        dotted = _dotted_name(relative_path, "")
        if dotted is not None:
            module_map[dotted] = relative_path

    # Deeper roots first: for nested roots, the most specific one is the name
    # the code inside it actually writes.
    for root_prefix in sorted((r for r in package_roots if r), key=lambda r: (-r.count("/"), r)):
        for relative_path in python_files:
            if not relative_path.startswith(f"{root_prefix}/"):
                continue
            dotted = _dotted_name(relative_path, root_prefix)
            if dotted is not None:
                module_map.setdefault(dotted, relative_path)

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
