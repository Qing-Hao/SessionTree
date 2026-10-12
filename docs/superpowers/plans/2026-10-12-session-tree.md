# Session Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web dashboard that shows every Claude Code CLI session on this Windows machine, grouped by repo and drawn on a git graph of local worktrees, with one-click Go to terminal, Resume and New session.

**Architecture:** A Python package `session_tree` reads three local sources (Claude Code's session registry, transcripts, git), assembles a board in a background thread every 3 s, and serves it from FastAPI on `127.0.0.1`. A single static HTML page polls `/api/board` and renders repo cards. Actions launch Windows Terminal (`wt`) or run a UI Automation PowerShell helper to focus an existing tab.

**Tech Stack:** Python 3.12, uv, FastAPI, uvicorn, psutil, pytest, httpx; plain HTML/CSS/JS; PowerShell 5.1 + UI Automation.

**Spec:** `docs/superpowers/specs/2026-10-12-session-tree-design.md` (mockup: `docs/superpowers/specs/2026-10-12-session-tree-mockup.html`)

## Global Constraints

- Windows only; sessions are Claude Code CLI in Windows Terminal.
- Server binds `127.0.0.1` only; default port `4317`; `--port` overrides.
- POST requires header `X-Session-Tree: 1`; Origin, when sent, must be a local host; Host header must be `127.0.0.1` or `localhost`.
- Never build shell strings; every subprocess gets an argument list.
- Registry: `~/.claude/sessions/<pid>.json`; ignore `*.key` files.
- Liveness: PID alive and process start within 2 s of `procStart` (FILETIME: `filetime / 10_000_000 - 11_644_473_600`).
- Closed sessions: transcripts modified within the last 24 h, not in the live registry.
- Transcripts read tail-first (last 256 KB); prompts truncated to 200 characters.
- Base branches: `main`, `master`, `Dev`, `dev`, `develop`, `Staging`, `staging`.
- Each git call times out after 5 s; git is spawned with `CREATE_NO_WINDOW`.
- Poll interval 3 s; "waiting" alert at 120 minutes or more.
- Python function names in snake_case; JS in camelCase.
- No outbound network requests from server or page (system fonts instead of web fonts).

## Review Focus

1. A session title containing `;` would split the `wt` command into two commands. Titles must have `;` replaced and paths must escape it (`\;`). Test in Task 5.
2. A DNS-rebinding page (Host `evil.com`) must not read prompts or trigger launches. Every request with a non-local Host gets 403. Test in Task 6.
3. A worktree whose folder was deleted but not pruned (`prunable` in porcelain) must be skipped, and its branch listed as loose, not crash the card. Test in Task 3.
4. A long transcript whose `ai-title` lies before the 256 KB tail must still show its title. Test in Task 2.
5. A branch with unrelated history (orphan) makes `merge-base` fail; the node must simply have no parent. Test in Task 3.

---

## File Structure

```
git_visualisation/
  .gitignore
  pyproject.toml
  session_tree/
    __init__.py
    __main__.py          # CLI entry: python -m session_tree
    registry.py          # read ~/.claude/sessions, liveness
    transcripts.py       # tail-read jsonl: title, prompt, cwd, branch; locate files
    git_graph.py         # worktrees, repo key, fork inference, cache, norm_path
    board.py             # session dicts, grouping, alerts, BoardService, lookups
    terminal.py          # LaunchSpec, wt command, Launcher (launch + focus)
    focus_tab.ps1        # UI Automation: select tab by title, bring window to front
    server.py            # FastAPI app factory, guards, routes
    static/index.html    # dashboard page
  tests/
    helpers.py           # temp git repo helpers
    test_registry.py
    test_transcripts.py
    test_git_graph.py
    test_board.py
    test_terminal.py
    test_server.py
```

---

### Task 1: Project scaffold and session registry

**Files:**
- Create: `.gitignore`, `pyproject.toml`, `session_tree/__init__.py`, `session_tree/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Produces: `RegistryEntry` (frozen dataclass: `pid:int, session_id:str, cwd:str, name:str, status:str, status_updated_ms:int, started_ms:int, kind:str, entrypoint:str, proc_start:float|None`), `filetime_to_unix(filetime:int)->float`, `is_alive(pid:int, proc_start:float|None)->bool`, `read_registry(directory:Path)->tuple[list[RegistryEntry], list[str]]`, constant `FILETIME_EPOCH_OFFSET`.

- [ ] **Step 1: Scaffold project**

`.gitignore`:
```
.venv/
__pycache__/
.pytest_cache/
*.egg-info/
```

`pyproject.toml`:
```toml
[project]
name = "session-tree"
version = "0.1.0"
description = "Local dashboard of Claude Code sessions by repo, worktree and branch."
requires-python = ">=3.12"
dependencies = ["fastapi>=0.110", "uvicorn>=0.29", "psutil>=5.9"]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["session_tree"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`session_tree/__init__.py`:
```python
"""Session Tree: local dashboard of Claude Code sessions by repo, worktree and branch."""
```

Run: `uv sync`
Expected: creates `.venv`, installs fastapi, uvicorn, psutil, pytest, httpx.

- [ ] **Step 2: Write the failing tests** — `tests/test_registry.py`

```python
import json
import subprocess
import sys
import time

import psutil
import pytest

from session_tree.registry import FILETIME_EPOCH_OFFSET, filetime_to_unix, read_registry

SID = "11111111-2222-3333-4444-555555555555"


def to_filetime(unix_s: float) -> int:
    return int((unix_s + FILETIME_EPOCH_OFFSET) * 10_000_000)


def write_entry(directory, pid, proc_start_ft, **extra):
    data = {"pid": pid, "sessionId": SID, "cwd": "C:\\x", "name": "x-1", "status": "idle",
            "statusUpdatedAt": 1, "startedAt": 1, "kind": "interactive", "entrypoint": "cli",
            "procStart": str(proc_start_ft), **extra}
    (directory / f"{pid}.json").write_text(json.dumps(data), encoding="utf-8")


def test_filetime_conversion():
    assert filetime_to_unix(134359513136022479) == pytest.approx(1791477713.60, abs=0.01)


def test_live_entry(tmp_path):
    me = psutil.Process()
    write_entry(tmp_path, me.pid, to_filetime(me.create_time()))
    entries, warnings = read_registry(tmp_path)
    assert [e.pid for e in entries] == [me.pid]
    assert entries[0].status == "idle" and entries[0].session_id == SID
    assert warnings == []


def test_pid_reuse_is_not_live(tmp_path):
    me = psutil.Process()
    write_entry(tmp_path, me.pid, to_filetime(me.create_time() - 3600))
    assert read_registry(tmp_path)[0] == []


def test_dead_pid_is_not_live(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    write_entry(tmp_path, child.pid, to_filetime(time.time()))
    assert read_registry(tmp_path)[0] == []


def test_malformed_file_is_skipped_and_key_files_ignored(tmp_path):
    (tmp_path / "123.json").write_text('{"pid":', encoding="utf-8")
    (tmp_path / "123.abc.key").write_text("secret", encoding="utf-8")
    entries, warnings = read_registry(tmp_path)
    assert entries == []
    assert len(warnings) == 1 and "123.json" in warnings[0]


def test_missing_directory(tmp_path):
    assert read_registry(tmp_path / "nope") == ([], [])
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'session_tree.registry'`

- [ ] **Step 4: Implement** — `session_tree/registry.py`

```python
"""Read Claude Code's per-process session registry and keep only live sessions."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import psutil

FILETIME_EPOCH_OFFSET = 11_644_473_600
START_TOLERANCE_S = 2.0


@dataclass(frozen=True)
class RegistryEntry:
    pid: int
    session_id: str
    cwd: str
    name: str
    status: str
    status_updated_ms: int
    started_ms: int
    kind: str
    entrypoint: str
    proc_start: float | None


def filetime_to_unix(filetime: int) -> float:
    """Convert a Windows FILETIME (100 ns ticks since 1601) to unix seconds."""
    return filetime / 10_000_000 - FILETIME_EPOCH_OFFSET


def is_alive(pid: int, proc_start: float | None) -> bool:
    """True when the PID runs and started at proc_start, which rules out PID reuse."""
    try:
        created = psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False
    return proc_start is None or abs(created - proc_start) <= START_TOLERANCE_S


def read_registry(directory: Path) -> tuple[list[RegistryEntry], list[str]]:
    """Return live registry entries plus a warning per unreadable file."""
    entries: list[RegistryEntry] = []
    warnings: list[str] = []
    if not directory.is_dir():
        return entries, warnings
    for path in sorted(directory.glob("*.json")):
        try:
            entry = _parse(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            warnings.append(f"Skipped {path.name}: {exc}")
            continue
        if is_alive(entry.pid, entry.proc_start):
            entries.append(entry)
    return entries, warnings


def _parse(raw: dict) -> RegistryEntry:
    proc = raw.get("procStart")
    return RegistryEntry(
        pid=int(raw["pid"]),
        session_id=str(raw["sessionId"]),
        cwd=str(raw["cwd"]),
        name=str(raw.get("name") or ""),
        status=str(raw.get("status") or ""),
        status_updated_ms=int(raw.get("statusUpdatedAt") or raw.get("updatedAt") or 0),
        started_ms=int(raw.get("startedAt") or 0),
        kind=str(raw.get("kind") or ""),
        entrypoint=str(raw.get("entrypoint") or ""),
        proc_start=filetime_to_unix(int(proc)) if proc else None,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_registry.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add .gitignore pyproject.toml uv.lock session_tree/__init__.py session_tree/registry.py tests/test_registry.py
git commit -m "feat: read Claude Code session registry with liveness check"
```

---

### Task 2: Transcripts

**Files:**
- Create: `session_tree/transcripts.py`
- Test: `tests/test_transcripts.py`

**Interfaces:**
- Produces: `SessionInfo` (frozen dataclass: `session_id:str, ai_title:str, last_prompt:str, cwd:str, git_branch:str, mtime:float`), `read_session_info(path:Path, tail_bytes:int=TAIL_BYTES, known_title:str="")->SessionInfo`, `find_transcripts(projects_dir:Path, max_age_s:float, now:float)->dict[str, Path]`, `find_transcript(projects_dir:Path, session_id:str)->Path|None`, `TranscriptCache` with `get(path:Path)->SessionInfo` and `locate(projects_dir:Path, session_id:str)->Path|None`.

- [ ] **Step 1: Write the failing tests** — `tests/test_transcripts.py`

```python
import json
import os
import time

from session_tree.transcripts import (TranscriptCache, find_transcript, find_transcripts,
                                      read_session_info)


def line(**obj):
    return json.dumps(obj, separators=(",", ":"))


def write(path, *lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_latest_title_and_real_prompt(tmp_path):
    f = tmp_path / "abc.jsonl"
    write(f,
          line(type="ai-title", aiTitle="Old title", sessionId="abc"),
          line(type="user", cwd="C:\\repo", gitBranch="main", message={"role": "user", "content": "fix the login bug"}),
          line(type="ai-title", aiTitle="New title", sessionId="abc"),
          line(type="user", cwd="C:\\repo", gitBranch="feat", message={"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}),
          line(type="user", cwd="C:\\repo", gitBranch="feat", message={"role": "user", "content": "<command-name>/clear</command-name>"}))
    info = read_session_info(f)
    assert info.session_id == "abc"
    assert info.ai_title == "New title"
    assert info.last_prompt == "fix the login bug"
    assert info.cwd == "C:\\repo" and info.git_branch == "feat"


def test_prompt_from_text_blocks_and_truncation(tmp_path):
    f = tmp_path / "s.jsonl"
    write(f, line(type="user", cwd="C:\\r", message={"content": [{"type": "text", "text": "look  at\nthis " + "x" * 300}, {"type": "image"}]}))
    info = read_session_info(f)
    assert info.last_prompt.startswith("look at this x")
    assert len(info.last_prompt) == 200


def test_title_before_tail_window_is_found(tmp_path):
    f = tmp_path / "s.jsonl"
    filler = [line(type="assistant", cwd="C:\\r", message={"content": "x" * 500}) for _ in range(50)]
    write(f, line(type="ai-title", aiTitle="Early title"), *filler,
          line(type="user", cwd="C:\\r", gitBranch="b", message={"content": "hello"}))
    info = read_session_info(f, tail_bytes=2048)
    assert info.ai_title == "Early title"
    assert info.last_prompt == "hello"


def test_known_title_skips_full_scan(tmp_path):
    f = tmp_path / "s.jsonl"
    write(f, line(type="ai-title", aiTitle="Early title"), *[line(type="assistant", cwd="C:\\r") for _ in range(100)])
    assert read_session_info(f, tail_bytes=512, known_title="Cached").ai_title == "Cached"


def test_find_transcripts_recent_only_and_skips_subagents(tmp_path):
    write(tmp_path / "p1" / "a.jsonl", line(type="user"))
    write(tmp_path / "p1" / "b.jsonl", line(type="user"))
    write(tmp_path / "p1" / "a" / "subagents" / "x.jsonl", line(type="user"))
    old = time.time() - 3 * 86400
    os.utime(tmp_path / "p1" / "b.jsonl", (old, old))
    assert set(find_transcripts(tmp_path, 86400, time.time())) == {"a"}
    assert find_transcript(tmp_path, "b") == tmp_path / "p1" / "b.jsonl"
    assert find_transcript(tmp_path, "zzz") is None


def test_cache_rereads_only_when_file_changes(tmp_path):
    f = tmp_path / "s.jsonl"
    write(f, line(type="ai-title", aiTitle="One"))
    cache = TranscriptCache()
    assert cache.get(f).ai_title == "One"
    write(f, line(type="ai-title", aiTitle="Two"), line(type="user", cwd="C:\\r", message={"content": "hi"}))
    assert cache.get(f).ai_title == "Two"


def test_cache_locates_transcript_by_session_id(tmp_path):
    write(tmp_path / "proj" / "sid-1.jsonl", line(type="user"))
    cache = TranscriptCache()
    assert cache.locate(tmp_path, "sid-1") == tmp_path / "proj" / "sid-1.jsonl"
    assert cache.locate(tmp_path, "sid-2") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcripts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'session_tree.transcripts'`

- [ ] **Step 3: Implement** — `session_tree/transcripts.py`

```python
"""Read session title, last prompt and location from Claude Code transcripts."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TAIL_BYTES = 256 * 1024
PROMPT_MAX = 200


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    ai_title: str
    last_prompt: str
    cwd: str
    git_branch: str
    mtime: float


def read_session_info(path: Path, tail_bytes: int = TAIL_BYTES, known_title: str = "") -> SessionInfo:
    """Parse the transcript tail; scan the whole file for a title only when needed."""
    found = _scan(_tail_lines(path, tail_bytes))
    title = found["ai_title"] or known_title or _find_title(path)
    return SessionInfo(session_id=path.stem, ai_title=title, last_prompt=found["last_prompt"],
                       cwd=found["cwd"], git_branch=found["git_branch"], mtime=path.stat().st_mtime)


def find_transcripts(projects_dir: Path, max_age_s: float, now: float) -> dict[str, Path]:
    """Map session id to transcript path for top-level files modified within max_age_s."""
    out: dict[str, Path] = {}
    if not projects_dir.is_dir():
        return out
    for project in projects_dir.iterdir():
        if not project.is_dir():
            continue
        for f in project.glob("*.jsonl"):
            try:
                if now - f.stat().st_mtime <= max_age_s:
                    out[f.stem] = f
            except OSError:
                continue
    return out


def find_transcript(projects_dir: Path, session_id: str) -> Path | None:
    """Find a session's transcript in any project folder, whatever its age."""
    if not projects_dir.is_dir():
        return None
    return next(iter(projects_dir.glob(f"*/{session_id}.jsonl")), None)


class TranscriptCache:
    """Cache parsed transcripts by file size and mtime, and paths by session id."""

    def __init__(self) -> None:
        self._infos: dict[Path, tuple[tuple[int, int], SessionInfo]] = {}
        self._paths: dict[str, Path] = {}

    def get(self, path: Path) -> SessionInfo:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = self._infos.get(path)
        if hit and hit[0] == key:
            return hit[1]
        info = read_session_info(path, known_title=hit[1].ai_title if hit else "")
        self._infos[path] = (key, info)
        return info

    def locate(self, projects_dir: Path, session_id: str) -> Path | None:
        path = self._paths.get(session_id)
        if path is None or not path.exists():
            path = find_transcript(projects_dir, session_id)
            if path is not None:
                self._paths[session_id] = path
        return path


def _tail_lines(path: Path, tail_bytes: int) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > tail_bytes:
            f.seek(size - tail_bytes)
            f.readline()  # drop the partial first line
        data = f.read()
    return data.decode("utf-8", errors="replace").splitlines()


def _scan(lines) -> dict[str, str]:
    out = {"ai_title": "", "last_prompt": "", "cwd": "", "git_branch": ""}
    for line in lines:
        if '"ai-title"' not in line and '"cwd"' not in line and '"type":"user"' not in line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "ai-title" and obj.get("aiTitle"):
            out["ai_title"] = str(obj["aiTitle"])
        if obj.get("cwd"):
            out["cwd"] = str(obj["cwd"])
            out["git_branch"] = str(obj.get("gitBranch") or "")
        if obj.get("type") == "user" and not obj.get("isMeta"):
            prompt = _prompt_text(obj.get("message"))
            if prompt:
                out["last_prompt"] = prompt
    return out


def _find_title(path: Path) -> str:
    title = ""
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"ai-title"' in line:
                try:
                    title = str(json.loads(line).get("aiTitle") or title)
                except (ValueError, AttributeError):
                    continue
    return title


def _prompt_text(message) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return ""
        content = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return ""
    text = " ".join(content.split())
    if not text or text.startswith("<"):
        return ""
    return text[:PROMPT_MAX]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcripts.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add session_tree/transcripts.py tests/test_transcripts.py
git commit -m "feat: read session title, prompt and location from transcripts"
```

---

### Task 3: Git graph

**Files:**
- Create: `session_tree/git_graph.py`, `tests/helpers.py`
- Test: `tests/test_git_graph.py`

**Interfaces:**
- Produces: `GitError(Exception)`, `norm_path(path:str)->str`, `repo_key(path:str)->str|None`, `Worktree` (dataclass `path, head, branch:str|None, is_main:bool`), `Node` (dataclass `id, branch:str|None, sha, path:str|None, is_main, ghost, detached, parent:str|None, ahead:int, behind:int`), `RepoGraph` (dataclass `root:str, name:str, nodes:list[Node], loose_branches:list[str]`), `list_worktrees(path)->list[Worktree]`, `branch_tips(path)->dict[str,str]`, `read_repo(path)->RepoGraph`, `GitGraphCache.get(path)->RepoGraph`, constant `BASES`.

- [ ] **Step 1: Write test helpers** — `tests/helpers.py`

```python
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
```

- [ ] **Step 2: Write the failing tests** — `tests/test_git_graph.py`

```python
import shutil

from helpers import git_commit, git_run, make_repo
from session_tree.git_graph import GitGraphCache, norm_path, read_repo, repo_key


def nodes_by_id(graph):
    return {n.id: n for n in graph.nodes}


def test_fork_chain_with_ghost_base(tmp_path):
    repo = make_repo(tmp_path / "proj")
    git_run(repo, "branch", "feat-a")
    git_run(repo, "worktree", "add", "-q", str(tmp_path / "wt-a"), "feat-a")
    git_commit(tmp_path / "wt-a", 2)
    git_run(tmp_path / "wt-a", "branch", "feat-b")
    git_run(repo, "worktree", "add", "-q", str(tmp_path / "wt-b"), "feat-b")
    git_commit(tmp_path / "wt-b", 3)
    git_run(repo, "checkout", "-q", "-b", "scratch")
    n = nodes_by_id(read_repo(str(repo)))
    assert n["main"].ghost and n["main"].path is None
    assert (n["feat-a"].parent, n["feat-a"].ahead, n["feat-a"].behind) == ("main", 2, 0)
    assert (n["feat-b"].parent, n["feat-b"].ahead) == ("feat-a", 3)
    assert n["scratch"].is_main and n["scratch"].parent == "main"


def test_twins_both_point_at_base(tmp_path):
    repo = make_repo(tmp_path / "proj")
    git_run(repo, "branch", "t1")
    git_run(repo, "worktree", "add", "-q", str(tmp_path / "wt-1"), "t1")
    git_commit(tmp_path / "wt-1")
    git_run(tmp_path / "wt-1", "branch", "t2")
    git_run(repo, "worktree", "add", "-q", str(tmp_path / "wt-2"), "t2")
    n = nodes_by_id(read_repo(str(repo)))
    assert (n["t1"].parent, n["t1"].ahead) == ("main", 1)
    assert (n["t2"].parent, n["t2"].ahead) == ("main", 1)


def test_detached_worktree_uses_folder_name(tmp_path):
    repo = make_repo(tmp_path / "proj")
    git_run(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "pr-1"))
    n = nodes_by_id(read_repo(str(repo)))
    assert n["pr-1"].detached and n["pr-1"].branch is None
    assert n["pr-1"].parent == "main"


def test_deleted_worktree_folder_is_skipped(tmp_path):
    repo = make_repo(tmp_path / "proj")
    git_run(repo, "branch", "p")
    git_run(repo, "worktree", "add", "-q", str(tmp_path / "wt-p"), "p")
    shutil.rmtree(tmp_path / "wt-p")
    graph = read_repo(str(repo))
    assert "p" not in nodes_by_id(graph)
    assert "p" in graph.loose_branches


def test_orphan_branch_has_no_parent(tmp_path):
    repo = make_repo(tmp_path / "proj")
    git_run(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "wt-o"))
    git_run(tmp_path / "wt-o", "checkout", "-q", "--orphan", "lonely")
    git_commit(tmp_path / "wt-o")
    n = nodes_by_id(read_repo(str(repo)))
    assert n["lonely"].parent is None


def test_linked_worktree_in_sibling_folder_shares_repo_key(tmp_path):
    repo = make_repo(tmp_path / "proj")
    git_run(repo, "worktree", "add", "-q", "-b", "side", str(tmp_path / "proj-side"))
    assert repo_key(str(repo)) == repo_key(str(tmp_path / "proj-side")) is not None
    assert repo_key(str(tmp_path)) is None
    graph = read_repo(str(tmp_path / "proj-side"))
    assert norm_path(graph.root) == norm_path(str(repo))
    assert graph.name == "proj"


def test_cache_recomputes_when_head_moves(tmp_path):
    repo = make_repo(tmp_path / "proj")
    git_run(repo, "worktree", "add", "-q", "-b", "f", str(tmp_path / "wt-f"))
    cache = GitGraphCache()
    first = cache.get(str(repo))
    assert cache.get(str(repo)) is first
    git_commit(tmp_path / "wt-f")
    assert nodes_by_id(cache.get(str(repo)))["f"].ahead == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_git_graph.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'session_tree.git_graph'`

- [ ] **Step 4: Implement** — `session_tree/git_graph.py`

```python
"""Read a repo's worktrees and infer which branch each worktree branch forked from."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

BASES = ("main", "master", "Dev", "dev", "develop", "Staging", "staging")
GIT_TIMEOUT_S = 5
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GitError(Exception):
    """A git command failed, timed out or could not start."""


@dataclass
class Worktree:
    path: str
    head: str
    branch: str | None
    is_main: bool


@dataclass
class Node:
    id: str
    branch: str | None
    sha: str
    path: str | None
    is_main: bool
    ghost: bool
    detached: bool
    parent: str | None = None
    ahead: int = 0
    behind: int = 0


@dataclass
class RepoGraph:
    root: str
    name: str
    nodes: list[Node]
    loose_branches: list[str]


def git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=GIT_TIMEOUT_S, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(str(exc)) from exc
    if r.returncode != 0:
        raise GitError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout.strip()


def norm_path(path: str) -> str:
    """Canonical form for comparing Windows paths (long names, case-folded)."""
    return os.path.normcase(os.path.realpath(path))


def repo_key(path: str) -> str | None:
    """Return the repo's shared git dir for a folder, or None when not in a repo."""
    try:
        return norm_path(git(path, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    except GitError:
        return None


def list_worktrees(path: str) -> list[Worktree]:
    """Worktrees from porcelain output; bare and prunable (folder deleted) ones are skipped."""
    raw: list[dict] = []
    for line in git(path, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            raw.append({"path": os.path.normpath(line[9:]), "head": "", "branch": None, "skip": False})
        elif not raw:
            continue
        elif line.startswith("HEAD "):
            raw[-1]["head"] = line[5:]
        elif line.startswith("branch "):
            raw[-1]["branch"] = line[7:].removeprefix("refs/heads/")
        elif line == "bare" or line.startswith("prunable"):
            raw[-1]["skip"] = True
    return [Worktree(w["path"], w["head"], w["branch"], i == 0) for i, w in enumerate(raw) if not w["skip"]]


def branch_tips(path: str) -> dict[str, str]:
    out = git(path, "for-each-ref", "--format=%(objectname) %(refname:short)", "refs/heads")
    return {name: sha for sha, name in (l.split(" ", 1) for l in out.splitlines() if " " in l)}


def read_repo(path: str) -> RepoGraph:
    return _build(path, list_worktrees(path), branch_tips(path))


class GitGraphCache:
    """Rebuild a repo graph only when a worktree HEAD or a branch tip moves."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[tuple, RepoGraph]] = {}

    def get(self, path: str) -> RepoGraph:
        wts = list_worktrees(path)
        tips = branch_tips(path)
        key = (tuple((w.path, w.head, w.branch) for w in wts), tuple(sorted(tips.items())))
        cache_key = norm_path(wts[0].path) if wts else norm_path(path)
        hit = self._cache.get(cache_key)
        if hit and hit[0] == key:
            return hit[1]
        graph = _build(path, wts, tips)
        self._cache[cache_key] = (key, graph)
        return graph


def _build(path: str, wts: list[Worktree], tips: dict[str, str]) -> RepoGraph:
    nodes = [Node(id=w.branch or os.path.basename(w.path), branch=w.branch, sha=w.head[:8], path=w.path,
                  is_main=w.is_main, ghost=False, detached=w.branch is None) for w in wts]
    sha_of = {n.id: w.head for n, w in zip(nodes, wts)}
    wt_branches = {w.branch for w in wts if w.branch}
    bases = {b for b in BASES if b in tips}
    for b in bases - wt_branches:
        sha_of[b] = tips[b]
    for node in nodes:
        if node.branch in bases:
            continue  # bases are roots
        found = _infer_parent(path, node.id, sha_of, bases)
        if found:
            node.parent, node.ahead, node.behind = found
    used = {n.parent for n in nodes if n.parent}
    for b in sorted((bases - wt_branches) & used):
        nodes.append(Node(id=b, branch=b, sha=tips[b][:8], path=None, is_main=False, ghost=True, detached=False))
    shown = {n.branch for n in nodes if n.branch}
    root = wts[0].path if wts else path
    return RepoGraph(root=root, name=os.path.basename(root.rstrip("\\/")), nodes=nodes,
                     loose_branches=sorted(b for b in tips if b not in shown))


def _infer_parent(cwd: str, me: str, sha_of: dict[str, str], bases: set[str]) -> tuple[str, int, int] | None:
    """Pick the ref whose fork point is nearest to me's tip; see spec section 5.2."""
    my_sha = sha_of[me]
    best: tuple[tuple[int, bool], str] | None = None
    for other, other_sha in sha_of.items():
        if other == me:
            continue
        try:
            mb = git(cwd, "merge-base", my_sha, other_sha)
            dist = 0 if mb == my_sha else int(git(cwd, "rev-list", "--count", f"{mb}..{my_sha}"))
        except (GitError, ValueError):
            continue  # unrelated histories
        if dist == 0 and other_sha != my_sha:
            continue  # other forked from me
        if dist == 0 and other not in bases:
            continue  # same commit: only a base may be the parent
        key = (dist, other not in bases)
        if best is None or key < best[0]:
            best = (key, other)
    if best is None:
        return None
    parent = best[1]
    behind, ahead = (int(x) for x in git(cwd, "rev-list", "--left-right", "--count", f"{sha_of[parent]}...{my_sha}").split())
    return parent, ahead, behind
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_git_graph.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add session_tree/git_graph.py tests/helpers.py tests/test_git_graph.py
git commit -m "feat: infer worktree branch fork graph with ahead/behind counts"
```

---

### Task 4: Board assembly and service

**Files:**
- Create: `session_tree/board.py`
- Test: `tests/test_board.py`

**Interfaces:**
- Consumes: `read_registry`, `RegistryEntry` (Task 1); `SessionInfo`, `TranscriptCache`, `find_transcripts` (Task 2); `GitError`, `GitGraphCache`, `norm_path`, `repo_key` (Task 3).
- Produces: `BoardService(claude_dir:Path, pinned:list[str]=())` with `refresh()->dict`, `snapshot()->dict`, `run_forever(stop:threading.Event, interval:float=3.0)->None`; pure `find_session(board:dict, session_id:str)->dict|None`, `find_node(board:dict, path:str)->dict|None`.
- Board JSON: `{generated_at:int(ms), home:str, repos:[Repo], alerts:[Alert], warnings:[str]}`.
  - Repo: `{key, name, root, nogit:bool, error:str|None, nodes:[Node], loose_branches:[str]}`
  - Node: `{id, branch, sha, path, where, is_main, ghost, detached, parent, ahead, behind, shared:bool, sessions:[Session]}`
  - Session: `{id, name, state:"busy"|"idle"|"other"|"closed", status_raw, live:bool, title, prompt, cwd, pid, since_ms, started_ms, ran_on, can_focus:bool, repo}`
  - Alert: `{kind:"shared"|"waiting"|"switched", repo, text, detail}`

- [ ] **Step 1: Write the failing tests** — `tests/test_board.py`

```python
import json
import time

import psutil

from helpers import git_run, make_repo
from session_tree.board import BoardService, find_node, find_session
from session_tree.registry import FILETIME_EPOCH_OFFSET

A = "aaaaaaaa-0000-0000-0000-000000000001"
B = "bbbbbbbb-0000-0000-0000-000000000002"
C = "cccccccc-0000-0000-0000-000000000003"


def register(claude, proc, sid, cwd, status="idle", status_ms=None):
    ft = int((proc.create_time() + FILETIME_EPOCH_OFFSET) * 10_000_000)
    d = claude / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{proc.pid}.json").write_text(json.dumps({
        "pid": proc.pid, "sessionId": sid, "cwd": str(cwd), "name": f"n-{sid[:4]}", "status": status,
        "statusUpdatedAt": status_ms or int(time.time() * 1000), "startedAt": 1, "kind": "interactive",
        "entrypoint": "cli", "procStart": str(ft)}), encoding="utf-8")


def transcript(claude, sid, cwd, title="", branch="main", prompt="do it"):
    d = claude / "projects" / "p"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "ai-title", "aiTitle": title})] if title else []
    lines.append(json.dumps({"type": "user", "cwd": str(cwd), "gitBranch": branch, "message": {"content": prompt}}, separators=(",", ":")))
    (d / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def node(board, node_id):
    return next(n for r in board["repos"] for n in r["nodes"] if n["id"] == node_id)


def test_session_maps_to_linked_worktree_case_insensitively(tmp_path):
    repo = make_repo(tmp_path / "proj")
    git_run(repo, "worktree", "add", "-q", "-b", "feat", str(repo / ".claude" / "worktrees" / "feat"))
    claude = tmp_path / "claude"
    register(claude, psutil.Process(), A, str(repo / ".claude" / "worktrees" / "feat").upper())
    transcript(claude, A, repo, title="Fix feat")
    board = BoardService(claude).refresh()
    feat = node(board, "feat")
    assert [s["id"] for s in feat["sessions"]] == [A]
    s = feat["sessions"][0]
    assert s["live"] and s["state"] == "idle" and s["title"] == "Fix feat" and s["can_focus"]
    assert node(board, "main")["sessions"] == []
    assert feat["where"] == ".claude\\worktrees\\feat"


def test_shared_worktree_and_closed_session(tmp_path):
    repo = make_repo(tmp_path / "proj")
    claude = tmp_path / "claude"
    me = psutil.Process()
    register(claude, me, A, repo, status="busy")
    register(claude, me.parent(), B, repo)
    transcript(claude, C, repo, title="Old work", branch="other")
    board = BoardService(claude).refresh()
    main = node(board, "main")
    assert main["shared"] is True
    states = {s["id"]: s["state"] for s in main["sessions"]}
    assert states == {A: "busy", B: "idle", C: "closed"}
    kinds = [a["kind"] for a in board["alerts"]]
    assert kinds.count("shared") == 1 and kinds.count("switched") == 1
    assert find_session(board, C)["live"] is False
    assert find_node(board, str(repo).upper())["id"] == "main"
    assert find_node(board, str(tmp_path)) is None


def test_folder_outside_git_and_waiting_alert(tmp_path):
    plain = tmp_path / "notes"
    plain.mkdir()
    claude = tmp_path / "claude"
    register(claude, psutil.Process(), A, plain, status_ms=int(time.time() * 1000) - 3 * 3600 * 1000)
    board = BoardService(claude).refresh()
    repo = board["repos"][0]
    assert repo["nogit"] is True and repo["error"] is None and repo["name"] == "notes"
    assert [a["kind"] for a in board["alerts"]] == ["waiting"]


def test_empty_when_no_claude_dir(tmp_path):
    board = BoardService(tmp_path / "missing").refresh()
    assert board["repos"] == [] and board["alerts"] == [] and board["warnings"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_board.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'session_tree.board'`

- [ ] **Step 3: Implement** — `session_tree/board.py`

```python
"""Assemble the dashboard board: repos, worktree nodes and the sessions in them."""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .git_graph import GitError, GitGraphCache, Node, norm_path, repo_key
from .registry import RegistryEntry, read_registry
from .transcripts import SessionInfo, TranscriptCache, find_transcripts

CLOSED_WINDOW_S = 24 * 3600
WAITING_ALERT_MS = 120 * 60 * 1000
REPO_KEY_TTL_S = 60
STATE_RANK = {"busy": 0, "idle": 1, "other": 2, "closed": 3}
log = logging.getLogger(__name__)


class BoardService:
    """Owns the caches and the latest board; refreshed by a background thread."""

    def __init__(self, claude_dir: Path, pinned: list[str] = ()) -> None:
        self.sessions_dir = claude_dir / "sessions"
        self.projects_dir = claude_dir / "projects"
        self.pinned = list(pinned)
        self.transcripts = TranscriptCache()
        self.graphs = GitGraphCache()
        self._repo_keys: dict[str, tuple[float, str | None]] = {}
        self._lock = threading.Lock()
        self._board: dict | None = None

    def refresh(self) -> dict:
        with self._lock:
            now = time.time()
            live, warnings = read_registry(self.sessions_dir)
            live_ids = {e.session_id for e in live}
            sessions = [_live_session(e, self._info(self.transcripts.locate(self.projects_dir, e.session_id), warnings)) for e in live]
            for sid, path in find_transcripts(self.projects_dir, CLOSED_WINDOW_S, now).items():
                if sid not in live_ids:
                    info = self._info(path, warnings)
                    if info and info.cwd:
                        sessions.append(_closed_session(info))
            now_ms = int(now * 1000)
            repos = self._repos(sessions, warnings)
            board = {"generated_at": now_ms, "home": str(Path.home()), "repos": repos,
                     "alerts": _alerts(repos, now_ms), "warnings": warnings}
            self._board = board
            return board

    def snapshot(self) -> dict:
        return self._board if self._board is not None else self.refresh()

    def run_forever(self, stop: threading.Event, interval: float = 3.0) -> None:
        while not stop.is_set():
            try:
                self.refresh()
            except Exception:  # keep serving the last board if one refresh fails
                log.exception("Board refresh failed")
            stop.wait(interval)

    def _info(self, path: Path | None, warnings: list[str]) -> SessionInfo | None:
        if path is None:
            return None
        try:
            return self.transcripts.get(path)
        except OSError as exc:
            warnings.append(f"Could not read {path.name}: {exc}")
            return None

    def _repo_key(self, cwd: str) -> str | None:
        hit = self._repo_keys.get(cwd)
        if hit and time.monotonic() - hit[0] < REPO_KEY_TTL_S:
            return hit[1]
        key = repo_key(cwd) if os.path.isdir(cwd) else None
        self._repo_keys[cwd] = (time.monotonic(), key)
        return key

    def _repos(self, sessions: list[dict], warnings: list[str]) -> list[dict]:
        groups: dict[str, dict] = {}
        for s in sessions:
            key = self._repo_key(s["cwd"])
            gkey = key or "folder:" + norm_path(s["cwd"])
            groups.setdefault(gkey, {"key": gkey, "git": key is not None, "cwd": s["cwd"], "sessions": []})["sessions"].append(s)
        for p in self.pinned:
            key = self._repo_key(p)
            if key and key not in groups:
                groups[key] = {"key": key, "git": True, "cwd": p, "sessions": []}
        repos = [self._repo(g, warnings) for g in groups.values()]
        return sorted(repos, key=_repo_rank)

    def _repo(self, g: dict, warnings: list[str]) -> dict:
        if not g["git"]:
            return _folder_repo(g)
        try:
            graph = self.graphs.get(g["cwd"])
        except GitError as exc:
            warnings.append(f"git failed in {g['cwd']}: {exc}")
            return _folder_repo(g, error=f"Cannot read git data: {exc}")
        nodes = [_node_dict(n, graph.root) for n in graph.nodes]
        for s in g["sessions"]:
            s["repo"] = g["key"]
            _attach(s, nodes)
        return _finish({"key": g["key"], "name": graph.name, "root": graph.root, "nogit": False, "error": None,
                        "nodes": nodes, "loose_branches": graph.loose_branches})


def find_session(board: dict, session_id: str) -> dict | None:
    return next((s for r in board["repos"] for n in r["nodes"] for s in n["sessions"] if s["id"] == session_id), None)


def find_node(board: dict, path: str) -> dict | None:
    """Return the worktree node whose path equals path (case-insensitive), if any."""
    target = norm_path(path)
    return next((n for r in board["repos"] for n in r["nodes"] if n["path"] and norm_path(n["path"]) == target), None)


def _live_session(e: RegistryEntry, info: SessionInfo | None) -> dict:
    title = info.ai_title if info else ""
    return {"id": e.session_id, "name": e.name or e.session_id[:8],
            "state": e.status if e.status in ("busy", "idle") else "other", "status_raw": e.status, "live": True,
            "title": title, "prompt": info.last_prompt if info else "", "cwd": e.cwd, "pid": e.pid,
            "since_ms": e.status_updated_ms, "started_ms": e.started_ms, "ran_on": info.git_branch if info else "",
            "can_focus": e.kind == "interactive" and e.entrypoint == "cli" and bool(title), "repo": None}


def _closed_session(info: SessionInfo) -> dict:
    return {"id": info.session_id, "name": info.session_id[:8], "state": "closed", "status_raw": "closed", "live": False,
            "title": info.ai_title, "prompt": info.last_prompt, "cwd": info.cwd, "pid": None,
            "since_ms": int(info.mtime * 1000), "started_ms": None, "ran_on": info.git_branch, "can_focus": False, "repo": None}


def _where(path: str, root: str, is_main: bool) -> str:
    if is_main:
        return "main checkout"
    if norm_path(path).startswith(norm_path(root) + os.sep):
        return os.path.relpath(path, root)
    return os.path.join("..", os.path.basename(path))


def _node_dict(n: Node, root: str) -> dict:
    d = asdict(n)
    d.update(where="" if n.ghost else _where(n.path, root, n.is_main), shared=False, sessions=[])
    return d


def _folder_repo(g: dict, error: str | None = None) -> dict:
    cwd = g["cwd"]
    exists = os.path.isdir(cwd)
    name = os.path.basename(cwd.rstrip("\\/")) or cwd
    for s in g["sessions"]:
        s["repo"] = g["key"]
    node = {"id": name, "branch": None, "sha": "", "path": cwd, "is_main": True, "ghost": False, "detached": False,
            "parent": None, "ahead": 0, "behind": 0, "shared": False, "sessions": list(g["sessions"]),
            "where": "not a git repository" if exists else "folder no longer exists"}
    return _finish({"key": g["key"], "name": name, "root": cwd, "nogit": error is None, "nodes": [node], "loose_branches": [],
                    "error": error if error else (None if exists else "Folder no longer exists")})


def _attach(s: dict, nodes: list[dict]) -> None:
    """Put the session on the worktree whose path is the longest prefix of its cwd."""
    cwd = norm_path(s["cwd"])
    best = None
    for n in nodes:
        if not n["path"]:
            continue
        p = norm_path(n["path"])
        if (cwd == p or cwd.startswith(p + os.sep)) and (best is None or len(p) > len(norm_path(best["path"]))):
            best = n
    target = best or next((n for n in nodes if n["is_main"]), nodes[0])
    target["sessions"].append(s)


def _finish(repo: dict) -> dict:
    for n in repo["nodes"]:
        n["sessions"].sort(key=lambda s: (STATE_RANK[s["state"]], -s["since_ms"]))
        n["shared"] = sum(1 for s in n["sessions"] if s["live"]) > 1
    return repo


def _repo_rank(r: dict) -> tuple:
    ss = [s for n in r["nodes"] for s in n["sessions"]]
    rank = (0 if any(s["state"] == "busy" for s in ss) else 1 if any(n["shared"] for n in r["nodes"])
            else 2 if any(s["live"] for s in ss) else 3)
    return rank, r["name"].lower()


def _alerts(repos: list[dict], now_ms: int) -> list[dict]:
    shared, waiting, switched = [], [], []
    for r in repos:
        for n in r["nodes"]:
            if n["shared"]:
                shared.append({"kind": "shared", "repo": r["key"], "text": f"{r['name']} · {n['id']}", "detail": ""})
            for s in n["sessions"]:
                if s["state"] == "idle" and now_ms - s["since_ms"] >= WAITING_ALERT_MS:
                    waiting.append((s["since_ms"], {"kind": "waiting", "repo": r["key"], "text": s["title"] or s["name"],
                                                    "detail": str((now_ms - s["since_ms"]) // 60000)}))
                if n["branch"] and s["ran_on"] and s["ran_on"] not in ("HEAD", n["branch"]):
                    switched.append({"kind": "switched", "repo": r["key"], "text": f"{r['name']} · {n['id']}",
                                     "detail": f"{s['ran_on']} → {n['branch']}"})
    return shared + [w for _, w in sorted(waiting, key=lambda x: x[0])] + switched
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_board.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add session_tree/board.py tests/test_board.py
git commit -m "feat: assemble board of repos, worktree nodes and sessions"
```

---

### Task 5: Terminal launching and tab focus

**Files:**
- Create: `session_tree/terminal.py`, `session_tree/focus_tab.ps1`
- Test: `tests/test_terminal.py`

**Interfaces:**
- Produces: `LaunchSpec` (frozen dataclass `program:list[str], cwd:str, title:str`), `LaunchError(Exception)`, `is_session_id(text:str)->bool`, `resume_spec(cwd, title, session_id)->LaunchSpec`, `new_session_spec(path, title)->LaunchSpec`, `wt_command(spec, program_path:str)->list[str]`, `Launcher` with `launch(spec)->dict` and `focus(title:str)->dict`.

- [ ] **Step 1: Write the failing tests** — `tests/test_terminal.py`

```python
import pytest

from session_tree import terminal
from session_tree.terminal import (LaunchError, Launcher, is_session_id, new_session_spec, resume_spec,
                                   wt_command)

SID = "28c2caf5-ee1f-478f-91c5-89724c10169c"


def test_resume_command_is_exact_argument_list():
    spec = resume_spec("C:\\Users\\me\\Agent-Platform", "Branch status", SID)
    assert wt_command(spec, "C:\\bin\\claude.exe") == [
        "wt", "-w", "0", "nt", "-d", "C:\\Users\\me\\Agent-Platform", "--title", "Branch status",
        "C:\\bin\\claude.exe", "--resume", SID]


def test_new_session_command():
    spec = new_session_spec("C:\\r\\wt-a", "feat-a")
    assert wt_command(spec, "claude.exe") == ["wt", "-w", "0", "nt", "-d", "C:\\r\\wt-a", "--title", "feat-a", "claude.exe"]


def test_semicolons_cannot_split_the_wt_command():
    cmd = wt_command(resume_spec("C:\\a;b", "plan; then build", SID), "claude.exe")
    assert cmd[5] == "C:\\a\\;b"
    assert cmd[7] == "plan, then build"


def test_session_id_validation():
    assert is_session_id(SID)
    assert not is_session_id("../../etc")
    assert not is_session_id(SID.upper() + "x")


def test_launch_uses_wt_when_available(monkeypatch):
    calls = []
    monkeypatch.setattr(terminal.shutil, "which", lambda exe: f"C:\\bin\\{exe}.exe")
    monkeypatch.setattr(terminal.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    result = Launcher().launch(new_session_spec("C:\\r", "main"))
    assert result["via"] == "wt" and calls[0][0][0] == "C:\\bin\\wt.exe"


def test_launch_falls_back_to_new_console(monkeypatch):
    calls = []
    monkeypatch.setattr(terminal.shutil, "which", lambda exe: None if exe == "wt" else "C:\\bin\\claude.exe")
    monkeypatch.setattr(terminal.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    result = Launcher().launch(new_session_spec("C:\\r", "main"))
    assert result["via"] == "console"
    assert calls[0][0] == ["C:\\bin\\claude.exe"] and calls[0][1]["cwd"] == "C:\\r"


def test_launch_without_claude_raises(monkeypatch):
    monkeypatch.setattr(terminal.shutil, "which", lambda exe: None)
    with pytest.raises(LaunchError, match="claude"):
        Launcher().launch(new_session_spec("C:\\r", "main"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_terminal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'session_tree.terminal'`

- [ ] **Step 3: Implement** — `session_tree/terminal.py`

```python
"""Open sessions in Windows Terminal and switch to a session's existing tab."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FOCUS_SCRIPT = Path(__file__).with_name("focus_tab.ps1")
FOCUS_TIMEOUT_S = 15
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


class LaunchError(Exception):
    """A session could not be started."""


@dataclass(frozen=True)
class LaunchSpec:
    program: list[str]
    cwd: str
    title: str


def is_session_id(text: str) -> bool:
    return bool(UUID_RE.match(text))


def resume_spec(cwd: str, title: str, session_id: str) -> LaunchSpec:
    return LaunchSpec(["claude", "--resume", session_id], cwd, title)


def new_session_spec(path: str, title: str) -> LaunchSpec:
    return LaunchSpec(["claude"], path, title)


def wt_command(spec: LaunchSpec, program_path: str) -> list[str]:
    """Windows Terminal new-tab command; ';' is escaped because wt splits commands on it."""
    return ["wt", "-w", "0", "nt", "-d", spec.cwd.replace(";", "\\;"), "--title", spec.title.replace(";", ","),
            program_path, *spec.program[1:]]


class Launcher:
    """Runs real processes; tests replace it with a fake."""

    def launch(self, spec: LaunchSpec) -> dict:
        program = shutil.which(spec.program[0])
        if program is None:
            raise LaunchError(f"{spec.program[0]} is not on PATH")
        wt = shutil.which("wt")
        if wt:
            cmd = wt_command(spec, program)
            subprocess.Popen([wt, *cmd[1:]], creationflags=_NO_WINDOW)
            return {"ok": True, "via": "wt", "command": cmd}
        subprocess.Popen([program, *spec.program[1:]], cwd=spec.cwd, creationflags=_NEW_CONSOLE)
        return {"ok": True, "via": "console", "command": spec.program}

    def focus(self, title: str) -> dict:
        """Run focus_tab.ps1; returns {result, method, tabs} as printed by the script."""
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-File", str(FOCUS_SCRIPT), "-Target", title],
                               capture_output=True, timeout=FOCUS_TIMEOUT_S, creationflags=_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"result": "error", "detail": str(exc)}
        out = r.stdout.decode("utf-8", errors="replace").strip().splitlines()
        try:
            return json.loads(out[-1])
        except (ValueError, IndexError):
            return {"result": "error", "detail": r.stderr.decode("utf-8", errors="replace").strip()[:300]}
```

- [ ] **Step 4: Implement** — `session_tree/focus_tab.ps1`

```powershell
# Select the Windows Terminal tab whose title matches -Target and bring its window to the front.
# Prints one JSON line: {result: ok|not_found|ambiguous|no_foreground, method, tabs}.
param([Parameter(Mandatory = $true)][string]$Target)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class SessionTreeW32 {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
}
"@

function Clean([string]$name) { ($name -replace '^[^\p{L}\p{N}]+', '').Trim() }
function Emit($obj) { $obj | ConvertTo-Json -Compress; exit 0 }

$A = [System.Windows.Automation.AutomationElement]
$winCond = New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, "CASCADIA_HOSTING_WINDOW_CLASS")
$tabCond = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)

$want = Clean $Target
$all = @()
$hits = @()
foreach ($w in $A::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $winCond)) {
  foreach ($t in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $tabCond)) {
    $clean = Clean $t.Current.Name
    $all += $clean
    if ($clean -eq $want) { $hits += , @($w, $t) }
  }
}
if ($hits.Count -eq 0) { Emit @{ result = "not_found"; method = $null; tabs = $all } }
if ($hits.Count -gt 1) { Emit @{ result = "ambiguous"; method = $null; tabs = $all } }

$win = $hits[0][0]
$tab = $hits[0][1]
$tab.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
$hwnd = [IntPtr]$win.Current.NativeWindowHandle
if ([SessionTreeW32]::IsIconic($hwnd)) { [void][SessionTreeW32]::ShowWindow($hwnd, 9) }

function IsFront() { Start-Sleep -Milliseconds 250; return ([SessionTreeW32]::GetForegroundWindow() -eq $hwnd) }

[void][SessionTreeW32]::SetForegroundWindow($hwnd)
if (IsFront) { Emit @{ result = "ok"; method = "A"; tabs = @() } }
# A synthetic Alt press lifts Windows' foreground lock for the next call.
[SessionTreeW32]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
[SessionTreeW32]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
[void][SessionTreeW32]::SetForegroundWindow($hwnd)
if (IsFront) { Emit @{ result = "ok"; method = "B"; tabs = @() } }
try { $win.SetFocus() } catch { }
if (IsFront) { Emit @{ result = "ok"; method = "C"; tabs = @() } }
Emit @{ result = "no_foreground"; method = $null; tabs = @() }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_terminal.py -v`
Expected: 7 passed

- [ ] **Step 6: Manual check of the focus script (read-only result, switches one window)**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File session_tree\focus_tab.ps1 -Target "no such tab title"`
Expected: one JSON line with `"result":"not_found"` and a `tabs` list of the open tab titles.

- [ ] **Step 7: Commit**

```bash
git add session_tree/terminal.py session_tree/focus_tab.ps1 tests/test_terminal.py
git commit -m "feat: launch sessions in Windows Terminal and focus existing tabs"
```

---

### Task 6: HTTP server and CLI entry

**Files:**
- Create: `session_tree/server.py`, `session_tree/__main__.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `BoardService.snapshot()`, `find_session`, `find_node` (Task 4); `Launcher`, `LaunchError`, `is_session_id`, `resume_spec`, `new_session_spec` (Task 5).
- Produces: `create_app(service, launcher) -> FastAPI`; routes `GET /`, `GET /api/board`, `POST /api/sessions/{sid}/focus` (body `{title?}`), `POST /api/sessions/{sid}/resume`, `POST /api/worktrees/new` (body `{path}`); CLI `python -m session_tree [--port 4317] [--open] [--claude-dir PATH]`.

- [ ] **Step 1: Write the failing tests** — `tests/test_server.py`

```python
import pytest
from fastapi.testclient import TestClient

from session_tree.server import create_app
from session_tree.terminal import LaunchError

LIVE = "aaaaaaaa-0000-0000-0000-000000000001"
CLOSED = "bbbbbbbb-0000-0000-0000-000000000002"
OK = {"X-Session-Tree": "1"}


def session(sid, live):
    return {"id": sid, "name": "n", "state": "idle" if live else "closed", "status_raw": "", "live": live,
            "title": "My title", "prompt": "", "cwd": "C:\\repo", "pid": 1 if live else None, "since_ms": 0,
            "started_ms": 0, "ran_on": "", "can_focus": live, "repo": "k"}


BOARD = {"generated_at": 0, "home": "C:\\Users\\me", "alerts": [], "warnings": [], "repos": [{
    "key": "k", "name": "repo", "root": "C:\\repo", "nogit": False, "error": None, "loose_branches": [],
    "nodes": [{"id": "main", "branch": "main", "sha": "abc", "path": "C:\\repo", "where": "main checkout",
               "is_main": True, "ghost": False, "detached": False, "parent": None, "ahead": 0, "behind": 0,
               "shared": False, "sessions": [session(LIVE, True), session(CLOSED, False)]}]}]}


class FakeService:
    def snapshot(self):
        return BOARD


class FakeLauncher:
    def __init__(self, fail=False):
        self.launched, self.focused, self.fail = [], [], fail

    def launch(self, spec):
        if self.fail:
            raise LaunchError("claude is not on PATH")
        self.launched.append(spec)
        return {"ok": True, "via": "wt", "command": ["wt"]}

    def focus(self, title):
        self.focused.append(title)
        return {"result": "ok", "method": "A", "tabs": []}


@pytest.fixture
def launcher():
    return FakeLauncher()


@pytest.fixture
def client(launcher):
    return TestClient(create_app(FakeService(), launcher), base_url="http://127.0.0.1:4317")


def test_board_and_index(client):
    assert client.get("/api/board").json()["repos"][0]["name"] == "repo"
    assert "Session Tree" in client.get("/").text


def test_foreign_host_is_rejected(client):
    assert client.get("/api/board", headers={"host": "evil.com:4317"}).status_code == 403


def test_post_without_header_is_rejected(client, launcher):
    assert client.post(f"/api/sessions/{CLOSED}/resume").status_code == 403
    assert launcher.launched == []


def test_post_with_foreign_origin_is_rejected(client, launcher):
    r = client.post(f"/api/sessions/{CLOSED}/resume", headers={**OK, "origin": "https://evil.com"})
    assert r.status_code == 403 and launcher.launched == []


def test_resume_known_session(client, launcher):
    r = client.post(f"/api/sessions/{CLOSED}/resume", headers={**OK, "origin": "http://127.0.0.1:4317"})
    assert r.status_code == 200
    spec = launcher.launched[0]
    assert spec.program == ["claude", "--resume", CLOSED] and spec.cwd == "C:\\repo" and spec.title == "My title"


def test_unknown_or_malformed_session_is_404(client, launcher):
    assert client.post("/api/sessions/cccccccc-0000-0000-0000-000000000003/resume", headers=OK).status_code == 404
    assert client.post("/api/sessions/..%2F..%2Fx/resume", headers=OK).status_code == 404
    assert launcher.launched == []


def test_focus_live_and_reject_closed(client, launcher):
    assert client.post(f"/api/sessions/{LIVE}/focus", headers=OK).json()["result"] == "ok"
    assert client.post(f"/api/sessions/{LIVE}/focus", headers=OK, json={"title": "Picked tab"}).status_code == 200
    assert launcher.focused == ["My title", "Picked tab"]
    assert client.post(f"/api/sessions/{CLOSED}/focus", headers=OK).status_code == 409


def test_new_session_only_for_known_worktree(client, launcher):
    assert client.post("/api/worktrees/new", headers=OK, json={"path": "c:\\REPO"}).status_code == 200
    assert launcher.launched[0].program == ["claude"] and launcher.launched[0].title == "main"
    assert client.post("/api/worktrees/new", headers=OK, json={"path": "C:\\Windows"}).status_code == 404


def test_missing_claude_returns_503():
    client = TestClient(create_app(FakeService(), FakeLauncher(fail=True)), base_url="http://127.0.0.1:4317")
    r = client.post(f"/api/sessions/{CLOSED}/resume", headers=OK)
    assert r.status_code == 503 and "claude" in r.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'session_tree.server'`

- [ ] **Step 3: Implement** — `session_tree/server.py`

```python
"""FastAPI app serving the dashboard page, the board JSON and session actions."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .board import find_node, find_session
from .terminal import LaunchError, is_session_id, new_session_spec, resume_spec

STATIC_DIR = Path(__file__).with_name("static")
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class FocusBody(BaseModel):
    title: str | None = Field(default=None, max_length=300)


class NewBody(BaseModel):
    path: str = Field(max_length=1000)


def require_local_post(request: Request) -> None:
    """Block cross-site requests: custom header plus a local Origin when one is sent."""
    if request.headers.get("x-session-tree") != "1":
        raise HTTPException(403, "Missing X-Session-Tree header")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname not in ALLOWED_HOSTS:
        raise HTTPException(403, "Origin not allowed")


def create_app(service, launcher) -> FastAPI:
    app = FastAPI(title="Session Tree", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_host_only(request: Request, call_next):
        # Rejects DNS-rebinding pages that reach 127.0.0.1 under another name.
        host = urlsplit("//" + (request.headers.get("host") or "")).hostname
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Host not allowed"}, status_code=403)
        return await call_next(request)

    def session_or_404(sid: str) -> dict:
        s = find_session(service.snapshot(), sid) if is_session_id(sid) else None
        if s is None:
            raise HTTPException(404, "Unknown session")
        return s

    def run(spec) -> dict:
        try:
            return launcher.launch(spec)
        except LaunchError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/board")
    def board() -> dict:
        return service.snapshot()

    @app.post("/api/sessions/{sid}/focus", dependencies=[Depends(require_local_post)])
    def focus(sid: str, body: FocusBody | None = None) -> dict:
        s = session_or_404(sid)
        if not s["live"]:
            raise HTTPException(409, "Session is closed; use Resume instead")
        title = body.title if body and body.title else s["title"]
        if not title:
            raise HTTPException(409, "Session has no title yet, so its tab cannot be found")
        return launcher.focus(title)

    @app.post("/api/sessions/{sid}/resume", dependencies=[Depends(require_local_post)])
    def resume(sid: str) -> dict:
        s = session_or_404(sid)
        return run(resume_spec(s["cwd"], s["title"] or s["name"], s["id"]))

    @app.post("/api/worktrees/new", dependencies=[Depends(require_local_post)])
    def new_session(body: NewBody) -> dict:
        node = find_node(service.snapshot(), body.path)
        if node is None:
            raise HTTPException(404, "Unknown worktree")
        return run(new_session_spec(node["path"], node["id"]))

    return app
```

- [ ] **Step 4: Implement** — `session_tree/__main__.py`

```python
"""Command line entry: python -m session_tree [--port N] [--open]."""
from __future__ import annotations

import argparse
import threading
import tomllib
import webbrowser
from pathlib import Path

import uvicorn

from .board import BoardService
from .server import create_app
from .terminal import Launcher

CONFIG_PATH = Path.home() / ".session-tree" / "config.toml"


def load_pinned(path: Path = CONFIG_PATH) -> list[str]:
    """Read pinned_repos from the optional config file."""
    try:
        return [str(p) for p in tomllib.loads(path.read_text(encoding="utf-8")).get("pinned_repos", [])]
    except (OSError, tomllib.TOMLDecodeError):
        return []


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="session_tree", description="Local dashboard of Claude Code sessions.")
    parser.add_argument("--port", type=int, default=4317)
    parser.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    parser.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude")
    args = parser.parse_args(argv)

    service = BoardService(args.claude_dir, pinned=load_pinned())
    service.refresh()
    stop = threading.Event()
    threading.Thread(target=service.run_forever, args=(stop,), daemon=True).start()
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Session Tree running at {url}  (Ctrl+C to stop)")
    if args.open:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    try:
        uvicorn.run(create_app(service, Launcher()), host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        stop.set()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Create a placeholder page so `GET /` works**

`session_tree/static/index.html` (replaced in Task 7):
```html
<!doctype html><title>Session Tree</title><p>Session Tree</p>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -v`
Expected: 9 passed

- [ ] **Step 7: Commit**

```bash
git add session_tree/server.py session_tree/__main__.py session_tree/static/index.html tests/test_server.py
git commit -m "feat: serve board API and session actions on localhost"
```

---

### Task 7: Dashboard page

**Files:**
- Modify: `session_tree/static/index.html` (full replacement)

**Interfaces:**
- Consumes: Board JSON from Task 4; `POST` routes from Task 6.

The page is the approved mockup (`docs/superpowers/specs/2026-10-12-session-tree-mockup.html`, version 4) with these changes: a full document with `<!doctype html>`; system fonts (Segoe UI Variable, Cascadia Mono) instead of Google Fonts; data from `GET /api/board` every 3 s; real actions through `POST`; toasts that report results, including the tab picker for `not_found`/`ambiguous`; a banner when the server is unreachable; an empty state; open `<details>` and the expanded session kept across refreshes; re-render only when the data changes or every 30 s for the clock.

- [ ] **Step 1: Write the page** — full content of `session_tree/static/index.html` is given in the implementation commit; it must implement exactly the elements listed above and in spec section 6.6, using these JSON fields: repo `key,name,root,nogit,error,nodes,loose_branches`; node `id,branch,sha,path,where,is_main,ghost,detached,parent,ahead,behind,shared,sessions`; session `id,name,state,status_raw,live,title,prompt,cwd,pid,since_ms,started_ms,ran_on,can_focus,repo`; alert `kind,repo,text,detail`.

- [ ] **Step 2: Run the server against real data**

Run: `uv run python -m session_tree --open`
Expected: console prints `Session Tree running at http://127.0.0.1:4317/`; the browser shows one card per repo with live sessions.

- [ ] **Step 3: Check the page renders without console errors**

Open the browser console on the page.
Expected: no errors; `GET /api/board` repeats every 3 s.

- [ ] **Step 4: Commit**

```bash
git add session_tree/static/index.html
git commit -m "feat: dashboard page with git graph cards and session actions"
```

---

### Task 8: Live evidence (spec 9.2)

**Files:** none (results reported to the user)

- [ ] **Step 1: Count matches.** Run `uv run python -c "from pathlib import Path; from session_tree.board import BoardService; b = BoardService(Path.home()/'.claude').refresh(); print(sum(s['live'] for r in b['repos'] for n in r['nodes'] for s in n['sessions']))"` and `(Get-Process claude).Count`. Expected: equal numbers (when every running claude.exe has a registry file).
- [ ] **Step 2: Graph matches the probe.** In the same Python output, print the Agent-Platform nodes. Expected: `Dev` ghost → `config/split-metadata-config-version` +5 → `feature/agent-visualisation` +17 → `feature/live-config` +7.
- [ ] **Step 3: Close detection.** Start a throwaway session `wt -w 0 nt -d <scratch dir> --title evidence claude`, confirm it appears, close the tab, confirm it turns Closed within 3 s.
- [ ] **Step 4: Resume.** Click Resume on that closed session; a new tab opens in the scratch folder with the conversation.
- [ ] **Step 5: Go to terminal from the browser.** Click Go to terminal on a session whose window is behind the browser; record the `method` returned.
- [ ] **Step 6: New session.** Click `+ session` on a worktree; `claude` starts in that folder. Close it afterwards.
- [ ] **Step 7: Full test run.** `uv run pytest -v`. Expected: all tests pass.
```
