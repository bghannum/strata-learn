"""File walking over a prepared source directory: .gitignore-based filtering
(signal reduction, not a safety boundary — the trust boundary is ADR-008's
scoped clone/extract, not this filter), size caps, and binary-file skip.

The symlink skip below IS a safety boundary, unlike the rest of this module:
git can track a symlink entry pointing anywhere on disk (e.g. `leak.py ->
/etc/passwd`), and `Path.is_file()`/`.read_bytes()` follow it transparently.
Without excluding symlinks here, a malicious repo could get an arbitrary
file's contents parsed as "source" by Layer A, and — once Phase 2's trade-off
extractor reads file contents to send to the LLM — actually exfiltrated to a
third-party API. Found via Codex's Phase 2 pre-push review.
"""

from dataclasses import dataclass
from pathlib import Path

import pathspec

from app.config import settings

# Skipped unconditionally, even absent a .gitignore entry — noise present in
# nearly every repo regardless of language/tooling, and directories like
# node_modules can be enormous, so this also keeps the walk itself fast.
DEFAULT_IGNORE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    ".turbo",
    "target",
    "vendor",
    ".idea",
    ".vscode",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".svg", ".pdf",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp4", ".mp3", ".wav", ".mov", ".avi",
    ".exe", ".dll", ".so", ".dylib", ".class", ".pyc", ".jar", ".wasm",
    ".db", ".sqlite", ".sqlite3",
    ".lock",
}


@dataclass(frozen=True)
class WalkedFile:
    path: Path  # absolute path on disk, for reading contents
    relative_path: str  # posix-style path relative to source_dir — this is CodeUnit.file_path


def walk_files(source_dir: Path) -> list[WalkedFile]:
    """Walk `source_dir`, returning files that survive .gitignore filtering, the
    default noise-directory skip, the binary check, and the per-file size cap.
    Deterministic and ordered — no LLM involved (Layer A, ADR-006)."""
    spec = _load_gitignore_spec(source_dir)
    results: list[WalkedFile] = []

    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue

        relative = path.relative_to(source_dir)
        if any(part in DEFAULT_IGNORE_DIRS for part in relative.parts[:-1]):
            continue

        relative_posix = relative.as_posix()
        if spec.match_file(relative_posix):
            continue

        if path.suffix.lower() in BINARY_EXTENSIONS:
            continue

        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0 or size > settings.max_file_size_bytes:
            continue

        if _looks_binary(path):
            continue

        results.append(WalkedFile(path=path, relative_path=relative_posix))

    return results


def _load_gitignore_spec(source_dir: Path) -> pathspec.PathSpec:
    """Root-level .gitignore only, for v1 — covers the common case without the
    added complexity of merging nested .gitignore scopes per directory."""
    gitignore_path = source_dir / ".gitignore"
    lines: list[str] = []
    if gitignore_path.is_file():
        lines = gitignore_path.read_text(errors="ignore").splitlines()
    return pathspec.PathSpec.from_lines("gitignore", lines)


def _looks_binary(path: Path, sniff_bytes: int = 8192) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(sniff_bytes)
    except OSError:
        return True
    return b"\x00" in chunk
