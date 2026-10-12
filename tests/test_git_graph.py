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

    def slow_rev_list(cwd, *args):
        if args[0] == "rev-list":
            raise git_graph.GitError("timed out after 5 seconds")
        return real(cwd, *args)

    monkeypatch.setattr(git_graph, "git", slow_rev_list)
    with pytest.raises(git_graph.GitError):
        read_repo(str(repo))


def commit(cwd, msg):
    git_run(cwd, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--allow-empty", "-q", "-m", msg)


def platform_like_repo(tmp_path):
    """Mirror Agent-Platform: stale local main, a merged branch, and a branch cut from a branch without a worktree."""
    make_repo(tmp_path / "seed")
    git_run(tmp_path, "clone", "-q", "--bare", "seed", "up.git")
    git_run(tmp_path, "clone", "-q", "up.git", "proj")
    repo = tmp_path / "proj"
    git_run(repo, "worktree", "add", "-q", "-b", "split", str(tmp_path / "proj-split"))
    commit(tmp_path / "proj-split", "split 1")
    commit(tmp_path / "proj-split", "split 2")
    git_run(repo, "checkout", "-q", "-b", "integ")
    git_run(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "merge", "-q", "--no-ff", "-m", "merge split", "split")
    commit(repo, "after merge")
    git_run(repo, "push", "-q", "origin", "integ:main")  # origin/main moves on; local main stays at the seed commit
    git_run(repo, "branch", "-q", "old", "HEAD~1")  # merged into origin/main, so never a parent
    git_run(repo, "checkout", "-q", "-b", "refactor", "origin/main")
    for i in range(3):
        commit(repo, f"refactor {i}")
    git_run(repo, "checkout", "-q", "-b", "live")  # cut from refactor's tip
    commit(repo, "live 1")
    git_run(repo, "checkout", "-q", "refactor")
    commit(repo, "refactor 3")
    git_run(repo, "checkout", "-q", "-b", "spare", "main")
    commit(repo, "spare 1")
    git_run(repo, "checkout", "-q", "live")
    git_run(repo, "branch", "-q", "-D", "integ")
    git_run(repo, "worktree", "add", "-q", "-b", "vis", str(tmp_path / "proj-vis"), "origin/main")
    return repo


def assert_platform_graph(graph):
    n = nodes_by_id(graph)
    assert (n["split"].parent, n["split"].ahead, n["split"].behind, n["split"].merged_into) == ("main", 2, 0, "origin/main")
    assert (n["vis"].parent, n["vis"].ahead, n["vis"].merged_into) == ("origin/main", 0, None)
    assert (n["refactor"].ghost, n["refactor"].kind, n["refactor"].parent, n["refactor"].ahead) == (True, "branch", "origin/main", 4)
    assert (n["live"].parent, n["live"].ahead, n["live"].behind, n["live"].is_main) == ("refactor", 1, 1, True)
    assert (n["main"].ghost, n["main"].kind, n["main"].upstream, n["main"].upstream_behind) == (True, "base", "origin/main", 4)
    assert (n["origin/main"].ghost, n["origin/main"].kind, n["origin/main"].parent) == (True, "remote", None)
    assert graph.loose_branches == ["old", "spare"]


def test_remote_bases_and_worktree_less_branches_are_parents(tmp_path):
    assert_platform_graph(read_repo(str(platform_like_repo(tmp_path))))


def test_git_without_ahead_behind_gives_the_same_graph(tmp_path, monkeypatch):
    repo = platform_like_repo(tmp_path)
    real = git_graph.git

    def old_git(cwd, *args):
        if any("%(ahead-behind" in a for a in args):
            raise git_graph.GitError("fatal: unknown field name: ahead-behind:abc", 128)
        return real(cwd, *args)

    monkeypatch.setattr(git_graph, "git", old_git)
    assert_platform_graph(read_repo(str(repo)))


def test_local_base_wins_a_tie_with_its_remote(tmp_path):
    make_repo(tmp_path / "seed")
    git_run(tmp_path, "clone", "-q", "seed", "proj")
    repo = tmp_path / "proj"
    git_run(repo, "worktree", "add", "-q", "-b", "feat", str(tmp_path / "wt-f"))
    commit(tmp_path / "wt-f", "feat 1")
    n = nodes_by_id(read_repo(str(repo)))
    assert (n["feat"].parent, n["feat"].ahead) == ("main", 1)
    assert n["main"].upstream == "origin/main" and n["main"].upstream_behind == 0
    assert "origin/main" not in n


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
