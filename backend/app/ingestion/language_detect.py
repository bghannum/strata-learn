"""Language detection: extension-based, with shebang sniffing as a fallback for
extensionless scripts. Scoped to Python + JS/TS for v1 (D10) — files in other
languages are walked but simply excluded from analysis, not treated as an error.
"""

from enum import Enum
from pathlib import Path


class Language(str, Enum):
    python = "python"
    javascript = "javascript"
    typescript = "typescript"


_EXTENSION_MAP: dict[str, Language] = {
    ".py": Language.python,
    ".pyi": Language.python,
    ".js": Language.javascript,
    ".jsx": Language.javascript,
    ".mjs": Language.javascript,
    ".cjs": Language.javascript,
    ".ts": Language.typescript,
    ".tsx": Language.typescript,
}

# matched against the shebang's interpreter token, e.g. "python3" from
# "#!/usr/bin/env python3"
_SHEBANG_MAP: dict[str, Language] = {
    "python": Language.python,
    "python3": Language.python,
    "node": Language.javascript,
}


def detect_language(path: Path) -> Language | None:
    suffix = path.suffix.lower()
    if suffix in _EXTENSION_MAP:
        return _EXTENSION_MAP[suffix]
    if suffix:
        # has an extension, just not one in v1 scope (D10) — not extensionless,
        # so don't fall through to shebang sniffing
        return None
    return _detect_from_shebang(path)


def _detect_from_shebang(path: Path) -> Language | None:
    try:
        with path.open("rb") as f:
            first_line = f.readline(200)
    except OSError:
        return None
    if not first_line.startswith(b"#!"):
        return None

    shebang = first_line.decode("utf-8", errors="ignore").strip()
    interpreter_path = shebang[2:].split()  # drop "#!", split interpreter from args
    if not interpreter_path:
        return None
    # "#!/usr/bin/env python3" -> args = ["python3"]; "#!/usr/bin/python3" -> ["python3"]
    interpreter = interpreter_path[-1] if interpreter_path[0].endswith("env") else interpreter_path[0]
    interpreter = interpreter.rsplit("/", 1)[-1]

    for key, lang in _SHEBANG_MAP.items():
        if interpreter.startswith(key):
            return lang
    return None
