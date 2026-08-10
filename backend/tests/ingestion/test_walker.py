from pathlib import Path

from app.ingestion.walker import walk_files


def test_respects_gitignore(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1")
    (tmp_path / ".env").write_text("SECRET=1")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01binary")
    (tmp_path / "ignored_dir").mkdir()
    (tmp_path / "ignored_dir" / "foo.py").write_text("y = 1")
    (tmp_path / ".gitignore").write_text("*.bin\n.env\nignored_dir/\n")

    paths = {f.relative_path for f in walk_files(tmp_path)}

    assert paths == {"app.py", ".gitignore"}


def test_skips_default_ignore_dirs_even_without_gitignore(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("console.log(1)")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]")

    paths = {f.relative_path for f in walk_files(tmp_path)}

    assert paths == {"app.py"}


def test_skips_binary_content_without_a_recognized_extension(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "weird_binary").write_bytes(b"\x00\x01\x02binary")

    paths = {f.relative_path for f in walk_files(tmp_path)}

    assert paths == {"a.py"}


def test_skips_empty_files(tmp_path: Path) -> None:
    (tmp_path / "empty.py").write_text("")
    (tmp_path / "real.py").write_text("x = 1")

    paths = {f.relative_path for f in walk_files(tmp_path)}

    assert paths == {"real.py"}


def test_skips_files_over_the_size_cap(tmp_path: Path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "max_file_size_bytes", 10)
    (tmp_path / "small.py").write_text("x = 1")
    (tmp_path / "big.py").write_text("x = 1" * 100)

    paths = {f.relative_path for f in walk_files(tmp_path)}

    assert paths == {"small.py"}
