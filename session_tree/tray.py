"""System-tray app: run the dashboard server in the background with Open, Restart and Quit."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn
from PIL import Image, ImageDraw

APP_DIR = Path.home() / ".session-tree"
SHORTCUT_NAME = "Session Tree.lnk"
BROWSER_PATHS = (
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
log = logging.getLogger(__name__)


def is_running(port: int, timeout: float = 1.5) -> bool:
    """True when a Session Tree server already answers on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/board", timeout=timeout) as r:
            return r.status == 200 and "repos" in json.loads(r.read())
    except (OSError, ValueError):
        return False


def app_window_command(url: str, candidates: list[str | None] | None = None) -> list[str] | None:
    """Edge/Chrome in app mode (own window, no tabs), or None when no such browser is found."""
    if candidates is None:
        candidates = [os.path.expandvars(p) for p in BROWSER_PATHS] + [shutil.which("msedge"), shutil.which("chrome")]
    exe = next((c for c in candidates if c and os.path.isfile(c)), None)
    return [exe, f"--app={url}"] if exe else None


def open_dashboard(url: str) -> None:
    cmd = app_window_command(url)
    if cmd:
        subprocess.Popen(cmd)
    else:
        webbrowser.open(url)


class ServerThread:
    """Run uvicorn in a background thread so the tray icon can own the main thread."""

    def __init__(self, app, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="session-tree-server", daemon=True)

    def start(self, timeout: float = 15.0) -> None:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Session Tree server did not start (is the port already in use?)")
            time.sleep(0.05)

    def stop(self, timeout: float = 10.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout)


def make_icon(size: int = 64) -> Image.Image:
    """Draw the tray icon: a round green badge with a bold white fork (drawn 4x, then downscaled)."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, s - 1, s - 1), fill=(22, 163, 108, 255))
    white, w, r = (255, 255, 255, 255), s // 9, s // 9
    cx, base, fork = s * 0.5, s * 0.78, s * 0.52
    lx, rx, tip = s * 0.30, s * 0.70, s * 0.26
    for end in ((cx, base), (lx, tip), (rx, tip)):
        d.line((cx, fork, *end), fill=white, width=w)
    for x, y in ((cx, base), (lx, tip), (rx, tip)):
        d.ellipse((x - r, y - r, x + r, y + r), fill=white)
    return img.resize((size, size), Image.LANCZOS)


def shortcut_spec(repo_dir: Path) -> dict[str, str]:
    """Shortcut that starts the tray app from this project's venv without a console window."""
    return {"target": str(repo_dir / ".venv" / "Scripts" / "pythonw.exe"), "args": "-m session_tree --tray",
            "workdir": str(repo_dir)}


def default_startup_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def default_start_menu_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def create_shortcut(path: Path, target: str, args: str, workdir: str, icon: str) -> None:
    # Values travel as environment variables, so no path is ever spliced into the script text.
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:ST_LNK); $s.TargetPath=$env:ST_TARGET; "
              "$s.Arguments=$env:ST_ARGS; $s.WorkingDirectory=$env:ST_DIR; $s.IconLocation=$env:ST_ICON; "
              "$s.Description='Session Tree dashboard'; $s.Save()")
    env = {**os.environ, "ST_LNK": str(path), "ST_TARGET": target, "ST_ARGS": args, "ST_DIR": workdir, "ST_ICON": icon}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True,
                   capture_output=True, creationflags=_NO_WINDOW)


def install(repo_dir: Path, startup_dir: Path | None = None, start_menu_dir: Path | None = None,
            app_dir: Path = APP_DIR) -> list[Path]:
    """Create the Startup (run at login) and Start menu shortcuts; returns their paths."""
    app_dir.mkdir(parents=True, exist_ok=True)
    ico = app_dir / "session-tree.ico"
    make_icon(256).save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    spec = shortcut_spec(repo_dir)
    made = []
    for folder in (startup_dir or default_startup_dir(), start_menu_dir or default_start_menu_dir()):
        path = folder / SHORTCUT_NAME
        create_shortcut(path, spec["target"], spec["args"], spec["workdir"], str(ico))
        made.append(path)
    return made


def uninstall(startup_dir: Path | None = None, start_menu_dir: Path | None = None) -> list[Path]:
    """Remove the shortcuts that install() created; returns the ones removed."""
    removed = []
    for folder in (startup_dir or default_startup_dir(), start_menu_dir or default_start_menu_dir()):
        path = folder / SHORTCUT_NAME
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def launch_detached(repo_dir: Path, port: int) -> None:
    """Start the tray app as its own windowless process."""
    spec = shortcut_spec(repo_dir)
    subprocess.Popen([spec["target"], "-m", "session_tree", "--tray", "--port", str(port)], cwd=spec["workdir"],
                     creationflags=_DETACHED, close_fds=True)


def run_tray(port: int, claude_dir: Path, pinned: list[str]) -> None:
    """Serve the dashboard and show the tray icon; a second start just opens the dashboard."""
    import pystray

    from .board import BoardService
    from .server import create_app
    from .terminal import Launcher

    url = f"http://127.0.0.1:{port}/"
    if is_running(port):
        open_dashboard(url)
        return
    _log_to_file()
    service = BoardService(claude_dir, pinned=pinned)
    stop = threading.Event()
    threading.Thread(target=service.run_forever, args=(stop,), name="session-tree-refresh", daemon=True).start()
    server = ServerThread(create_app(service, Launcher()), port)
    server.start()
    log.info("Session Tree tray started on %s", url)

    def quit_app(icon, _item=None):
        server.stop()
        stop.set()
        icon.stop()

    def restart(icon, _item=None):
        quit_app(icon)
        launch_detached(Path(__file__).resolve().parent.parent, port)

    menu = pystray.Menu(
        pystray.MenuItem("Open dashboard", lambda icon, item: open_dashboard(url), default=True),
        pystray.MenuItem("Restart", restart),
        pystray.MenuItem("Quit", quit_app),
    )
    pystray.Icon("session-tree", make_icon(64), "Session Tree", menu).run()


def _log_to_file() -> None:
    """pythonw has no console, so send output and logs to ~/.session-tree/session-tree.log."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        f = open(APP_DIR / "session-tree.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
