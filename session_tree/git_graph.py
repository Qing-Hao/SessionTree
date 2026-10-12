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
    kind: str = "worktree"  # worktree | base | remote | branch (the last three are ghosts)
    merged_into: str | None = None
    upstream: str | None = None
    upstream_behind: int = 0


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


def remote_base_tips(path: str) -> dict[str, str]:
    """Remote-tracking refs of base branches, such as origin/Dev, keyed by short name."""
    out = git(path, "for-each-ref", "--format=%(objectname) %(refname)", "refs/remotes")
    tips = {}
    for sha, ref in (l.split(" ", 1) for l in out.splitlines() if " " in l):
        name = ref.removeprefix("refs/remotes/")
        if "/" in name and name.split("/", 1)[1] in BASES:
            tips[name] = sha
    return tips


def read_repo(path: str) -> RepoGraph:
    return _build(path, list_worktrees(path), branch_tips(path), remote_base_tips(path))


class GitGraphCache:
    """Rebuild a repo graph only when a worktree HEAD or a branch tip moves."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[tuple, RepoGraph]] = {}

    def get(self, path: str) -> RepoGraph:
        wts = list_worktrees(path)
        tips = branch_tips(path)
        remotes = remote_base_tips(path)
        key = (tuple((w.path, w.head, w.branch) for w in wts), tuple(sorted(tips.items())), tuple(sorted(remotes.items())))
        cache_key = norm_path(wts[0].path) if wts else norm_path(path)
        hit = self._cache.get(cache_key)
        if hit and hit[0] == key:
            return hit[1]
        graph = _build(path, wts, tips, remotes)
        self._cache[cache_key] = (key, graph)
        return graph


def _build(path: str, wts: list[Worktree], tips: dict[str, str], remotes: dict[str, str]) -> RepoGraph:
    nodes = [Node(id=w.branch or os.path.basename(w.path), branch=w.branch, sha=w.head[:8], path=w.path,
                  is_main=w.is_main, ghost=False, detached=w.branch is None) for w in wts]
    by_id = {n.id: n for n in nodes}
    sha_of = {n.id: w.head for n, w in zip(nodes, wts)}
    ref_of = {n.id: f"refs/heads/{w.branch}" for n, w in zip(nodes, wts) if w.branch}
    wt_branches = {w.branch for w in wts if w.branch}
    local_bases = {b for b in BASES if b in tips}
    bases = local_bases | set(remotes)
    for b in local_bases - wt_branches:
        sha_of[b], ref_of[b] = tips[b], f"refs/heads/{b}"
    for r, sha in remotes.items():
        sha_of[r], ref_of[r] = sha, f"refs/remotes/{r}"
    merged = _merged_into(path, {b: sha_of[b] for b in bases})
    # Unmerged branches without a worktree can be parents too; merged ones never beat the base holding them.
    for b, sha in tips.items():
        if b not in sha_of and b not in merged:
            sha_of[b], ref_of[b] = sha, f"refs/heads/{b}"

    dists: dict[str, dict[str, tuple[int, int] | None]] = {}
    parents: dict[str, str | None] = {}
    todo = [n.id for n in nodes if n.branch not in bases]  # bases are roots
    # git spawns are slow on Windows; each ref's counts are independent, so run them in parallel.
    with ThreadPoolExecutor(max_workers=GIT_WORKERS) as pool:
        while todo:
            dists.update(zip(todo, pool.map(lambda me: _distances(path, me, sha_of, ref_of), todo)))
            parents = {me: _pick_parent(me, bases, dists[me]) for me in dists}
            _break_loops(path, parents, sha_of, ref_of, bases, dists)
            # A worktree-less branch picked as a parent needs a parent of its own: next round.
            todo = sorted({p for p in parents.values() if p and p not in dists and p not in bases})

    used, frontier = set(), list(by_id)
    while frontier:
        p = parents.get(frontier.pop())
        if p and p not in used and p not in by_id:
            used.add(p)
            frontier.append(p)
    for p in sorted(used):
        kind = "remote" if p in remotes else "base" if p in local_bases else "branch"
        nodes.append(Node(id=p, branch=p, sha=sha_of[p][:8], path=None, is_main=False, ghost=True, detached=False, kind=kind))
    for node in nodes:
        parent = parents.get(node.id)
        if parent:
            node.parent = parent
            node.behind, node.ahead = dists[node.id][parent]
            if node.ahead and node.branch in merged:
                node.merged_into = merged[node.branch]
        if node.kind in ("worktree", "base") and node.branch in local_bases:
            node.upstream = _upstream_of(node.branch, remotes)
            if node.upstream:
                count = git(path, "rev-list", "--count", f"{sha_of[node.id]}..{remotes[node.upstream]}")
                node.upstream_behind = int(count)
    shown = {n.branch for n in nodes if n.branch}
    root = wts[0].path if wts else path
    return RepoGraph(root=root, name=os.path.basename(root.rstrip("\\/")), nodes=nodes,
                     loose_branches=sorted(b for b in tips if b not in shown))


def _base_rank(ref: str) -> tuple[int, bool]:
    """Sort key for bases: BASES order, then a local branch before its remote-tracking copy."""
    name = ref.split("/", 1)[1] if "/" in ref else ref
    return (BASES.index(name), "/" in ref) if name in BASES else (len(BASES), False)


def _upstream_of(base: str, remotes: dict[str, str]) -> str | None:
    """The remote-tracking copy of a local base, preferring origin."""
    if f"origin/{base}" in remotes:
        return f"origin/{base}"
    return next((r for r in sorted(remotes) if r.split("/", 1)[1] == base), None)


def _merged_into(cwd: str, base_shas: dict[str, str]) -> dict[str, str]:
    """Map each local branch whose tip is already inside a base to the first such base."""
    merged: dict[str, str] = {}
    for base in sorted(base_shas, key=_base_rank):
        out = git(cwd, "for-each-ref", "--format=%(refname)", f"--merged={base_shas[base]}", "refs/heads")
        for ref in out.splitlines():
            merged.setdefault(ref.removeprefix("refs/heads/"), base)
    return merged


def _distances(cwd: str, me: str, sha_of: dict[str, str], ref_of: dict[str, str]) -> dict[str, tuple[int, int] | None]:
    """(behind, ahead) of me against every other ref; None where the histories are unrelated."""
    my_sha = sha_of[me]
    total = int(git(cwd, "rev-list", "--count", my_sha))
    others = [o for o in sha_of if o != me]
    by_ref = {ref_of[o]: o for o in others if o in ref_of}
    counts: dict[str, tuple[int, int]] = {}
    if by_ref:
        try:
            out = git(cwd, "for-each-ref", f"--format=%(ahead-behind:{my_sha}) %(refname)", *by_ref)
        except GitError as exc:
            if exc.returncode is None or "ahead-behind" not in str(exc):
                raise
            out = ""  # git older than 2.41 has no ahead-behind atom: count each pair below
        for line in out.splitlines():
            behind, ahead, ref = line.split(" ", 2)
            if ref in by_ref:
                counts[by_ref[ref]] = (int(behind), int(ahead))
    for o in others:
        if o not in counts:
            counts[o] = _left_right(cwd, sha_of[o], my_sha)
    # Every one of my commits missing from the other ref means no shared history at all.
    return {o: None if ahead == total else (behind, ahead) for o, (behind, ahead) in counts.items()}


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


def _break_loops(cwd: str, parents: dict[str, str | None], sha_of: dict[str, str], ref_of: dict[str, str],
                 bases: set[str], dists: dict) -> None:
    """Re-pick parents inside loops, keeping each pick whose parent's reflog shows it made the fork commit."""
    for _ in range(len(parents)):
        loop = _find_cycle(parents)
        if not loop:
            return
        repick = {me for me in loop if not _made_fork(cwd, sha_of[me], parents[me], sha_of, ref_of)}
        for me in repick or loop:
            parents[me] = _pick_parent(me, bases, dists[me], exclude=loop)


def _made_fork(cwd: str, my_sha: str, parent: str, sha_of: dict[str, str], ref_of: dict[str, str]) -> bool:
    """True when parent committed the commit I split from, rather than merely starting there too."""
    ref = ref_of.get(parent, "")
    if not ref.startswith("refs/heads/"):
        return False
    fork = git(cwd, "merge-base", my_sha, sha_of[parent])
    try:
        positions = git(cwd, "reflog", "show", "--format=%H", ref, "--").split()
    except GitError:
        return False  # no reflog: expired or never written
    return fork in positions[:-1]  # the oldest entry is where the branch was created


def _pick_parent(me: str, bases: set[str], dists: dict[str, tuple[int, int] | None],
                 exclude: set[str] = frozenset()) -> str | None:
    """Pick the ref whose fork point is nearest to me's tip; see spec section 5.2."""
    best: tuple[tuple, str] | None = None
    for other, counts in dists.items():
        if other in exclude or counts is None:
            continue
        behind, dist = counts
        if dist == 0 and behind > 0:
            continue  # other forked from me
        if dist == 0 and other not in bases:
            continue  # same commit: only a base may be the parent
        # Ties: bases first, in BASES order with local before remote, then by name so the result is stable.
        key = (dist, other not in bases, _base_rank(other) if other in bases else (0, False), other)
        if best is None or key < best[0]:
            best = (key, other)
    return best[1] if best else None


def _left_right(cwd: str, parent_sha: str, my_sha: str) -> tuple[int, int]:
    """Return (behind, ahead) of my_sha against parent_sha."""
    behind, ahead = git(cwd, "rev-list", "--left-right", "--count", f"{parent_sha}...{my_sha}").split()
    return int(behind), int(ahead)
