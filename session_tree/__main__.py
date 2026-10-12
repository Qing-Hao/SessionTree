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
