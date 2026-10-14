import http.server
import socket
import subprocess
import threading

from session_tree.server import create_app
from session_tree.tray import (ServerThread, app_window_command, install, is_running, make_icon,
                               shortcut_spec, uninstall)


class FakeService:
    def snapshot(self):
        return {"generated_at": 0, "home": "", "repos": [], "alerts": [], "warnings": []}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_server_thread_serves_and_stops_and_is_detected():
    port = free_port()
    server = ServerThread(create_app(FakeService(), launcher=None), port)
    server.start()
    assert is_running(port)
    server.stop()
    assert not is_running(port)
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))  # port is free again


def test_other_service_on_the_port_is_not_session_tree():
    class Plain(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello")

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Plain)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert not is_running(httpd.server_address[1])
    finally:
        httpd.shutdown()


def test_closed_port_is_not_running():
    assert not is_running(free_port())


def test_app_window_uses_first_installed_browser(tmp_path):
    edge = tmp_path / "msedge.exe"
    edge.write_text("")
    cmd = app_window_command("http://127.0.0.1:4317/", [str(tmp_path / "missing.exe"), None, str(edge)])
    assert cmd == [str(edge), "--app=http://127.0.0.1:4317/"]
    assert app_window_command("http://x/", [str(tmp_path / "missing.exe")]) is None


def test_shortcut_runs_pythonw_from_the_project_venv(tmp_path):
    assert shortcut_spec(tmp_path) == {"target": str(tmp_path / ".venv" / "Scripts" / "pythonw.exe"),
                                       "args": "-m session_tree --tray", "workdir": str(tmp_path)}


def test_icon_is_square_rgba():
    img = make_icon(64)
    assert img.size == (64, 64) and img.mode == "RGBA"


def test_cli_install_creates_shortcuts_then_starts_the_tray(monkeypatch):
    import session_tree.__main__ as cli
    calls = []
    monkeypatch.setattr(cli.tray, "install", lambda repo: calls.append(("install", repo)) or [])
    monkeypatch.setattr(cli.tray, "launch_detached", lambda repo, port: calls.append(("launch", port)))
    cli.main(["--install"])
    assert calls == [("install", cli.REPO_DIR), ("launch", 4380)]


def test_cli_tray_runs_the_tray_app(monkeypatch):
    import session_tree.__main__ as cli
    seen = {}
    monkeypatch.setattr(cli.tray, "run_tray", lambda port, claude_dir, pinned: seen.update(port=port))
    cli.main(["--tray", "--port", "4400"])
    assert seen == {"port": 4400}


def read_shortcut(path):
    script = "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath + '|' + $s.Arguments + '|' + $s.WorkingDirectory"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                         env={**__import__("os").environ, "LNK": str(path)}, check=True)
    return out.stdout.strip().split("|")


def test_install_and_uninstall_shortcuts(tmp_path):
    repo, startup, menu, app = (tmp_path / d for d in ("repo", "startup", "menu", "app"))
    for d in (repo, startup, menu):
        d.mkdir()
    made = install(repo, startup_dir=startup, start_menu_dir=menu, app_dir=app)
    assert made == [startup / "Session Tree.lnk", menu / "Session Tree.lnk"]
    target, args, workdir = read_shortcut(made[0])
    assert target.lower() == str(repo / ".venv" / "Scripts" / "pythonw.exe").lower()
    assert args == "-m session_tree --tray" and workdir.lower() == str(repo).lower()
    assert (app / "session-tree.ico").exists()
    assert uninstall(startup_dir=startup, start_menu_dir=menu) == made
    assert not any(p.exists() for p in made)
