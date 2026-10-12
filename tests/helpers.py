"""Helpers that build throwaway git repos for tests."""
import subprocess
from pathlib import Path


def git_run(cwd, *args) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)
    return r.stdout.strip()


def git_commit(cwd, n: int = 1) -> None:
    for i in range(n):
        git_run(cwd, "-c", "user.name=t", "-c", "user.email=t@example.com",
                "commit", "--allow-empty", "-q", "-m", f"c{i}")


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    git_run(root, "init", "-q", "-b", "main")
    git_commit(root)
    return root
