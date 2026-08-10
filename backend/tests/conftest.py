import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def git_fixture_repo(tmp_path: Path) -> Path:
    """A minimal real git repo (one commit, one file) for exercising the
    git-clone path without depending on network access."""
    repo_dir = tmp_path / "fixture-repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hi')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@test.com", "-c", "user.name=test", "commit", "-q", "-m", "init"],
        cwd=repo_dir,
        check=True,
    )
    return repo_dir
