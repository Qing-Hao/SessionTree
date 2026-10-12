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
