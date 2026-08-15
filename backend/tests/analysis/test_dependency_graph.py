from typing import ClassVar

from app.analysis.dependency_graph import FileInfo, build_dependency_graph, detect_python_package_roots
from app.analysis.parser import parse_source
from app.ingestion.language_detect import Language


def _build(files_src: dict[str, bytes], language: Language) -> dict:
    files = [FileInfo(relative_path=p, language=language) for p in files_src]
    parsed = [p for path, src in files_src.items() if (p := parse_source(src, path, language)) is not None]
    return build_dependency_graph(parsed, files)


def _edges(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["source"], e["target"], e["kind"]) for e in graph["edges"]}


class TestPythonResolution:
    # A regression fixture for two real bugs found in Phase 1: `from . import x`
    # resolving to itself instead of the sibling module, and `from ..config
    # import settings` resolving to nothing at all.
    FILES: ClassVar[dict[str, bytes]] = {
        "app/__init__.py": b"",
        "app/main.py": (
            b"from fastapi import FastAPI\n"
            b"from app.config import settings\n"
            b"from app.api import repos\n"
        ),
        "app/config.py": b"import os\n",
        "app/api/__init__.py": b"",
        "app/api/repos.py": (
            b"from app.db.models import Repo\n"
            b"from . import quizzes\n"
            b"from ..config import settings\n"
        ),
        "app/api/quizzes.py": b"from ..db import session\n",
        "app/db/__init__.py": b"",
        "app/db/models.py": b"",
        "app/db/session.py": b"",
    }

    def test_absolute_import_resolves_to_file(self):
        edges = _edges(_build(self.FILES, Language.python))
        assert ("app/main.py", "app/config.py", "imports") in edges

    def test_from_package_import_submodule_resolves_to_submodule_not_init(self):
        # regression: `from app.api import repos` used to be indistinguishable
        # from importing an attribute of app/api/__init__.py
        edges = _edges(_build(self.FILES, Language.python))
        assert ("app/main.py", "app/api/repos.py", "imports") in edges
        assert ("app/main.py", "app/api/__init__.py", "imports") not in edges

    def test_bare_relative_import_resolves_to_sibling_submodule_not_self(self):
        # regression: `from . import quizzes` in app/api/repos.py used to
        # resolve to app/api/repos.py itself (a self-loop)
        edges = _edges(_build(self.FILES, Language.python))
        assert ("app/api/repos.py", "app/api/quizzes.py", "imports") in edges
        assert ("app/api/repos.py", "app/api/repos.py", "imports") not in edges

    def test_dotted_relative_import_climbs_correct_number_of_levels(self):
        # regression: `from ..config import settings` in app/api/repos.py used
        # to resolve to nothing (missing edge)
        edges = _edges(_build(self.FILES, Language.python))
        assert ("app/api/repos.py", "app/config.py", "imports") in edges

    def test_relative_import_submodule_disambiguation(self):
        # `from ..db import session` — "session" is app/db/session.py, a
        # submodule, not an attribute of app/db/__init__.py
        edges = _edges(_build(self.FILES, Language.python))
        assert ("app/api/quizzes.py", "app/db/session.py", "imports") in edges
        assert ("app/api/quizzes.py", "app/db/__init__.py", "imports") not in edges

    def test_absolute_import_of_deep_submodule(self):
        edges = _edges(_build(self.FILES, Language.python))
        assert ("app/api/repos.py", "app/db/models.py", "imports") in edges

    def test_unresolvable_stdlib_import_becomes_external_node(self):
        edges = _edges(_build(self.FILES, Language.python))
        assert ("app/main.py", "external:fastapi", "imports_external") in edges
        assert ("app/config.py", "external:os", "imports_external") in edges


class TestJsTsResolution:
    def test_relative_import_resolves_via_extension_fallback(self):
        files = {
            "src/App.tsx": b'import { Button } from "./components/Button";\n',
            "src/components/Button.tsx": b"export function Button() { return null; }\n",
        }
        edges = _edges(_build(files, Language.typescript))
        assert ("src/App.tsx", "src/components/Button.tsx", "imports") in edges

    def test_relative_import_resolves_via_index_fallback(self):
        files = {
            "src/App.tsx": b'import utils from "../utils";\n',
            "utils/index.ts": b"export const x = 1;\n",
        }
        edges = _edges(_build(files, Language.typescript))
        assert ("src/App.tsx", "utils/index.ts", "imports") in edges

    def test_bare_import_becomes_external_node(self):
        files = {"src/App.tsx": b'import React from "react";\n'}
        edges = _edges(_build(files, Language.typescript))
        assert ("src/App.tsx", "external:react", "imports_external") in edges

    def test_scoped_package_name_keeps_scope(self):
        files = {"src/App.tsx": b'import { x } from "@scope/pkg/sub";\n'}
        edges = _edges(_build(files, Language.typescript))
        assert ("src/App.tsx", "external:@scope/pkg", "imports_external") in edges

    def test_commonjs_require_is_captured(self):
        files = {
            "src/a.js": b'const b = require("./b");\n',
            "src/b.js": b"module.exports = {};\n",
        }
        edges = _edges(_build(files, Language.javascript))
        assert ("src/a.js", "src/b.js", "imports") in edges

    def test_unresolvable_relative_import_is_dropped_not_fabricated(self):
        files = {"src/App.tsx": b'import x from "./does-not-exist";\n'}
        graph = _build(files, Language.typescript)
        assert _edges(graph) == set()


class TestPackageRootResolution:
    """#57: absolute imports resolved only against the repo root, so a repo
    whose Python lives one directory down — a monorepo, a src-layout — had
    every absolute import fall through to an external node and appeared to have
    no internal dependencies at all."""

    def test_detects_marker_file_directory_as_a_root(self) -> None:
        roots = detect_python_package_roots(
            ["backend/pyproject.toml", "backend/app/main.py", "frontend/package.json"]
        )
        assert "backend" in roots
        assert "" in roots  # repo root is always a candidate

    def test_detects_src_layout(self) -> None:
        roots = detect_python_package_roots(["pyproject.toml", "src/mypkg/__init__.py", "src/mypkg/mod.py"])
        assert "src" in roots

    def test_detects_topmost_package_parent_without_a_marker_file(self) -> None:
        roots = detect_python_package_roots(["libs/pkg/__init__.py", "libs/pkg/sub/__init__.py"])
        assert "libs" in roots

    def test_monorepo_absolute_imports_resolve(self) -> None:
        # No __init__.py anywhere: namespace packages, which is how this repo's
        # own backend is laid out — the __init__.py walk alone found nothing.
        files_src = {
            "backend/app/main.py": b"from app.config import settings\n",
            "backend/app/config.py": b"import os\n",
        }
        files = [FileInfo(relative_path=p, language=Language.python) for p in files_src]
        parsed = [
            p for path, src in files_src.items() if (p := parse_source(src, path, Language.python)) is not None
        ]
        roots = detect_python_package_roots([*files_src, "backend/pyproject.toml"])

        graph = build_dependency_graph(parsed, files, roots)

        assert ("backend/app/main.py", "backend/app/config.py", "imports") in _edges(graph)

    def test_src_layout_absolute_imports_resolve(self) -> None:
        files_src = {
            "src/mypkg/__init__.py": b"",
            "src/mypkg/cli.py": b"from mypkg.core import run\n",
            "src/mypkg/core.py": b"def run():\n    pass\n",
        }
        files = [FileInfo(relative_path=p, language=Language.python) for p in files_src]
        parsed = [
            p for path, src in files_src.items() if (p := parse_source(src, path, Language.python)) is not None
        ]
        roots = detect_python_package_roots([*files_src, "pyproject.toml"])

        graph = build_dependency_graph(parsed, files, roots)

        assert ("src/mypkg/cli.py", "src/mypkg/core.py", "imports") in _edges(graph)

    def test_repo_root_imports_still_resolve_and_win_collisions(self) -> None:
        # A repo-root name that already resolved must keep resolving to the same
        # file — the package-root pass only fills gaps, never redirects.
        files_src = {
            "app/config.py": b"TOP = 1\n",
            "backend/app/config.py": b"NESTED = 1\n",
            "caller.py": b"from app.config import TOP\n",
        }
        files = [FileInfo(relative_path=p, language=Language.python) for p in files_src]
        parsed = [
            p for path, src in files_src.items() if (p := parse_source(src, path, Language.python)) is not None
        ]
        roots = detect_python_package_roots([*files_src, "backend/pyproject.toml"])

        graph = build_dependency_graph(parsed, files, roots)

        assert ("caller.py", "app/config.py", "imports") in _edges(graph)
        assert ("caller.py", "backend/app/config.py", "imports") not in _edges(graph)

    def test_unresolvable_absolute_import_is_still_external(self) -> None:
        files_src = {"backend/app/main.py": b"import requests\n"}
        files = [FileInfo(relative_path=p, language=Language.python) for p in files_src]
        parsed = [
            p for path, src in files_src.items() if (p := parse_source(src, path, Language.python)) is not None
        ]
        roots = detect_python_package_roots([*files_src, "backend/pyproject.toml"])

        graph = build_dependency_graph(parsed, files, roots)

        assert ("backend/app/main.py", "external:requests", "imports_external") in _edges(graph)

    def test_default_package_roots_preserve_repo_root_only_behavior(self) -> None:
        # build_dependency_graph is called without roots in a few tests and
        # from older code paths; that must still mean "repo root only".
        files_src = {
            "backend/app/main.py": b"from app.config import settings\n",
            "backend/app/config.py": b"import os\n",
        }
        files = [FileInfo(relative_path=p, language=Language.python) for p in files_src]
        parsed = [
            p for path, src in files_src.items() if (p := parse_source(src, path, Language.python)) is not None
        ]

        graph = build_dependency_graph(parsed, files)

        assert ("backend/app/main.py", "backend/app/config.py", "imports") not in _edges(graph)
