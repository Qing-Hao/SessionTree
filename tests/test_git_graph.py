import shutil

import pytest

from helpers import git_commit, git_run, make_repo
from session_tree import git_graph
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


def test_siblings_from_a_non_tip_commit_do_not_point_at_each_other(tmp_path):
    repo = make_repo(tmp_path / "proj")
    git_run(repo, "checkout", "-q", "-b", "upstream")  # stands in for origin/main ahead of local main
    git_commit(repo, 2)
    for b in ("feat-a", "feat-b"):
        git_run(repo, "worktree", "add", "-q", "-b", b, str(tmp_path / b))
        git_run(tmp_path / b, "-c", "user.name=t", "-c", "user.email=t@example.com",
                "commit", "--allow-empty", "-q", "-m", f"work on {b}")  # distinct, else same sha
    git_run(repo, "checkout", "-q", "main")
    git_run(repo, "branch", "-q", "-D", "upstream")
    n = nodes_by_id(read_repo(str(repo)))
    assert (n["feat-a"].parent, n["feat-a"].ahead) == ("main", 3)
    assert (n["feat-b"].parent, n["feat-b"].ahead) == ("main", 3)


def test_git_timeout_is_an_error_not_unrelated_history(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    git_run(repo, "worktree", "add", "-q", "-b", "f", str(tmp_path / "wt-f"))
    real = git_graph.git

    def slow_merge_base(cwd, *args):
        if args[0] == "merge-base":
            raise git_graph.GitError("timed out after 5 seconds")
        return real(cwd, *args)

    monkeypatch.setattr(git_graph, "git", slow_merge_base)
    with pytest.raises(git_graph.GitError):
        read_repo(str(repo))


def test_tied_bases_resolve_in_bases_order(tmp_path):
    repo = make_repo(tmp_path / "proj")
    for b in ("staging", "develop", "dev"):  # no case twins: refs collide on Windows
        git_run(repo, "branch", b)
    git_run(repo, "worktree", "add", "-q", "-b", "feat", str(tmp_path / "wt-f"))
    git_commit(tmp_path / "wt-f")
    git_run(repo, "checkout", "-q", "-b", "scratch")
    for _ in range(3):
        assert nodes_by_id(read_repo(str(repo)))["feat"].parent == "main"


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
    # A distinct message, otherwise the empty root commit hashes identical to main's first commit.
    git_run(tmp_path / "wt-o", "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "--allow-empty", "-q", "-m", "orphan root")
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
