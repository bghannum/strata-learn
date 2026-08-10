from pathlib import Path

import pytest

from app.ingestion.language_detect import Language, detect_language


@pytest.mark.parametrize(
    "filename,content,expected",
    [
        ("a.py", "", Language.python),
        ("a.pyi", "", Language.python),
        ("b.ts", "", Language.typescript),
        ("c.tsx", "", Language.typescript),
        ("d.jsx", "", Language.javascript),
        ("e.mjs", "", Language.javascript),
        ("no_ext_env", "#!/usr/bin/env python3\nprint(1)", Language.python),
        ("no_ext_direct", "#!/usr/bin/python3\nprint(1)", Language.python),
        ("no_ext_node", "#!/usr/bin/env node\nconsole.log(1)", Language.javascript),
        ("no_shebang", "just text", None),
        ("e.rb", "", None),
    ],
)
def test_detect_language(tmp_path: Path, filename: str, content: str, expected: Language | None) -> None:
    path = tmp_path / filename
    path.write_text(content)
    assert detect_language(path) == expected
