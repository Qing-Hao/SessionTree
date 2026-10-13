"""Open sessions in Windows Terminal and switch to a session's existing tab."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FOCUS_SCRIPT = Path(__file__).with_name("focus_tab.ps1")
FOCUS_TIMEOUT_S = 15
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


class LaunchError(Exception):
    """A session could not be started."""


@dataclass(frozen=True)
class LaunchSpec:
    program: list[str]
    cwd: str
    title: str


def is_session_id(text: str) -> bool:
    return bool(UUID_RE.match(text))


def resume_spec(cwd: str, title: str, session_id: str) -> LaunchSpec:
    return LaunchSpec(["claude", "--resume", session_id], cwd, title)


def new_session_spec(path: str, title: str) -> LaunchSpec:
    return LaunchSpec(["claude"], path, title)


def wt_command(spec: LaunchSpec, program_path: str) -> list[str]:
    """New Windows Terminal window running the program inside PowerShell; ';' is escaped as wt splits on it."""
    # Args are known values only (resolved exe path, validated session id), so the PowerShell string is safe.
    ps = "& '" + program_path.replace("'", "''") + "'" + "".join(" " + a for a in spec.program[1:])
    return ["wt", "-w", "new", "nt", "-d", spec.cwd.replace(";", "\\;"), "--title", spec.title.replace(";", ","),
            "powershell.exe", "-NoLogo", "-NoExit", "-Command", ps]


def launch_env(env: dict[str, str]) -> dict[str, str]:
    """Copy env without variables that would make the new claude think it is a child session or colourless."""
    drop = {"NO_COLOR", "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENTRYPOINT"}
    return {k: v for k, v in env.items()
            if k not in drop and not k.startswith(("CLAUDE_CODE_SESSION_", "CLAUDE_CODE_MESSAGING_"))}


class Launcher:
    """Runs real processes; tests replace it with a fake."""

    def launch(self, spec: LaunchSpec) -> dict:
        program = shutil.which(spec.program[0])
        if program is None:
            raise LaunchError(f"{spec.program[0]} is not on PATH")
        wt = shutil.which("wt")
        env = launch_env(dict(os.environ))
        if wt:
            cmd = wt_command(spec, program)
            subprocess.Popen([wt, *cmd[1:]], creationflags=_NO_WINDOW, env=env)
            return {"ok": True, "via": "wt", "command": cmd}
        subprocess.Popen([program, *spec.program[1:]], cwd=spec.cwd, creationflags=_NEW_CONSOLE, env=env)
        return {"ok": True, "via": "console", "command": spec.program}

    def focus(self, title: str) -> dict:
        """Run focus_tab.ps1; returns {result, method, tabs} as printed by the script."""
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-File", str(FOCUS_SCRIPT), "-Target", title],
                               capture_output=True, timeout=FOCUS_TIMEOUT_S, creationflags=_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"result": "error", "detail": str(exc)}
        out = r.stdout.decode("utf-8", errors="replace").strip().splitlines()
        try:
            return json.loads(out[-1])
        except (ValueError, IndexError):
            return {"result": "error", "detail": r.stderr.decode("utf-8", errors="replace").strip()[:300]}
