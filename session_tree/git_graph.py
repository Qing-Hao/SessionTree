"""Read a repo's worktrees and infer which branch each worktree branch forked from."""
from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

BASES = ("main", "master", "Dev", "dev", "develop", "Staging", "staging")
GIT_TIMEOUT_S = 5
GIT_WORKERS = 8
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GitError(Exception):
    """A git command failed, timed out or could not start; returncode is None unless git exited."""

    def __init__(self, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


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
        raise GitError(r.stderr.strip() or f"git {' '.join(args)} failed", r.returncode)
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
    children = [n for n in nodes if n.branch not in bases]  # bases are roots
    # git spawns are slow on Windows; pairs are independent, so run them in parallel.
    pairs = [(n.id, other) for n in children for other in sha_of if other != n.id]
    with ThreadPoolExecutor(max_workers=GIT_WORKERS) as pool:
        dists = dict(zip(pairs, pool.map(lambda p: _fork_distance(path, sha_of[p[0]], sha_of[p[1]]), pairs)))
    parents = {n.id: _pick_parent(n.id, sha_of, bases, dists) for n in children}
    # Siblings forked from a commit that is no branch tip pick each other; re-pick outside the loop.
    for _ in range(len(parents)):
        cycle = _find_cycle(parents)
        if not cycle:
            break
        for me in cycle:
            parents[me] = _pick_parent(me, sha_of, bases, dists, exclude=cycle)
    for node in children:
        parent = parents[node.id]
        if parent:
            behind, ahead = _left_right(path, sha_of[parent], sha_of[node.id])
            node.parent, node.ahead, node.behind = parent, ahead, behind
    used = {n.parent for n in nodes if n.parent}
    for b in sorted((bases - wt_branches) & used):
        nodes.append(Node(id=b, branch=b, sha=tips[b][:8], path=None, is_main=False, ghost=True, detached=False))
    shown = {n.branch for n in nodes if n.branch}
    root = wts[0].path if wts else path
    return RepoGraph(root=root, name=os.path.basename(root.rstrip("\\/")), nodes=nodes,
                     loose_branches=sorted(b for b in tips if b not in shown))


def _fork_distance(cwd: str, my_sha: str, other_sha: str) -> int | None:
    """Commits on my_sha since it split from other_sha; None for unrelated histories."""
    try:
        mb = git(cwd, "merge-base", my_sha, other_sha)
    except GitError as exc:
        if exc.returncode == 1:
            return None  # no common ancestor
        raise  # timeouts and other failures must surface, not be cached as "unrelated"
    return 0 if mb == my_sha else int(git(cwd, "rev-list", "--count", f"{mb}..{my_sha}"))


def _find_cycle(parents: dict[str, str | None]) -> set[str]:
    """Return the members of one parent loop, or an empty set."""
    for start in parents:
        chain: list[str] = []
        cur = start
        while cur in parents and cur not in chain:
            chain.append(cur)
            cur = parents[cur]
        if cur in chain:
            return set(chain[chain.index(cur):])
    return set()


def _pick_parent(me: str, sha_of: dict[str, str], bases: set[str], dists: dict,
                 exclude: set[str] = frozenset()) -> str | None:
    """Pick the ref whose fork point is nearest to me's tip; see spec section 5.2."""
    best: tuple[tuple, str] | None = None
    for other, other_sha in sha_of.items():
        dist = dists.get((me, other))
        if other == me or other in exclude or dist is None:
            continue
        if dist == 0 and other_sha != sha_of[me]:
            continue  # other forked from me
        if dist == 0 and other not in bases:
            continue  # same commit: only a base may be the parent
        # Ties: bases first, in BASES order, then by name so the result is stable.
        key = (dist, other not in bases, BASES.index(other) if other in bases else 0, other)
        if best is None or key < best[0]:
            best = (key, other)
    return best[1] if best else None


def _left_right(cwd: str, parent_sha: str, my_sha: str) -> tuple[int, int]:
    """Return (behind, ahead) of my_sha against parent_sha."""
    behind, ahead = git(cwd, "rev-list", "--left-right", "--count", f"{parent_sha}...{my_sha}").split()
    return int(behind), int(ahead)
