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
# Claude Code's registry writes status busy, shell, idle or waiting (waiting carries waitingFor).
STATE_RANK = {"waiting": 0, "busy": 1, "shell": 1, "idle": 2, "other": 3, "closed": 4}
ACTIVE_STATES = ("waiting", "busy", "shell")
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
            nodes = [_node_dict(n, graph.root) for n in graph.nodes]
            if not nodes:
                return _folder_repo(g, error="This repository has no worktree to show (bare repository)")
            for s in g["sessions"]:
                s["repo"] = g["key"]
                _attach(s, nodes)
        except GitError as exc:
            warnings.append(f"git failed in {g['cwd']}: {exc}")
            return _folder_repo(g, error=f"Cannot read git data: {exc}")
        except Exception as exc:  # one odd repo must not freeze the whole board
            log.exception("Building repo %s failed", g["cwd"])
            return _folder_repo(g, error=f"Could not build the branch graph: {exc}")
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
            "state": e.status if e.status in (*ACTIVE_STATES, "idle") else "other", "status_raw": e.status,
            "waiting_for": e.waiting_for, "live": True,
            "title": title, "prompt": info.last_prompt if info else "", "cwd": e.cwd, "pid": e.pid,
            "since_ms": e.status_updated_ms, "started_ms": e.started_ms, "ran_on": info.git_branch if info else "",
            "can_focus": e.kind == "interactive" and e.entrypoint == "cli" and bool(title), "repo": None}


def _closed_session(info: SessionInfo) -> dict:
    return {"id": info.session_id, "name": info.session_id[:8], "state": "closed", "status_raw": "closed",
            "waiting_for": "", "live": False,
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
    target = best or next((n for n in nodes if n["is_main"]), None) or nodes[0]
    target["sessions"].append(s)


def _finish(repo: dict) -> dict:
    for n in repo["nodes"]:
        n["sessions"].sort(key=lambda s: (STATE_RANK[s["state"]], -s["since_ms"]))
        n["shared"] = sum(1 for s in n["sessions"] if s["live"]) > 1
    return repo


def _repo_rank(r: dict) -> tuple:
    ss = [s for n in r["nodes"] for s in n["sessions"]]
    rank = (0 if any(s["state"] in ACTIVE_STATES for s in ss) else 1 if any(n["shared"] for n in r["nodes"])
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
