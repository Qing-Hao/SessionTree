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
        self.launched, self.focused, self.opened, self.fail = [], [], [], fail

    def open_editor(self, path):
        if self.fail:
            raise LaunchError("VS Code (code) is not on PATH")
        self.opened.append(path)
        return {"ok": True, "command": ["Code.exe", path]}

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


def test_open_code_only_for_known_worktree(client, launcher):
    assert client.post("/api/worktrees/code", json={"path": "C:\\repo"}).status_code == 403
    assert client.post("/api/worktrees/code", headers=OK, json={"path": "c:\\REPO"}).status_code == 200
    assert launcher.opened == ["C:\\repo"]
    assert client.post("/api/worktrees/code", headers=OK, json={"path": "C:\\Windows"}).status_code == 404
    assert launcher.opened == ["C:\\repo"]


def test_missing_vscode_returns_503():
    client = TestClient(create_app(FakeService(), FakeLauncher(fail=True)), base_url="http://127.0.0.1:4317")
    r = client.post("/api/worktrees/code", headers=OK, json={"path": "C:\\repo"})
    assert r.status_code == 503 and r.json()["detail"] == "VS Code (code) is not on PATH"


def test_missing_claude_returns_503():
    client = TestClient(create_app(FakeService(), FakeLauncher(fail=True)), base_url="http://127.0.0.1:4317")
    r = client.post(f"/api/sessions/{CLOSED}/resume", headers=OK)
    assert r.status_code == 503 and "claude" in r.json()["detail"]
