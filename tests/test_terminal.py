import pytest

from session_tree import terminal
from session_tree.terminal import (LaunchError, Launcher, is_session_id, new_session_spec, resume_spec,
                                   wt_command)

SID = "28c2caf5-ee1f-478f-91c5-89724c10169c"


def test_resume_command_runs_claude_inside_powershell():
    spec = resume_spec("C:\\Users\\me\\Agent-Platform", "Branch status", SID)
    assert wt_command(spec, "C:\\bin\\claude.exe") == [
        "wt", "-w", "new", "nt", "-d", "C:\\Users\\me\\Agent-Platform", "--title", "Branch status",
        "powershell.exe", "-NoLogo", "-NoExit", "-Command", f"& 'C:\\bin\\claude.exe' --resume {SID}"]


def test_new_session_command():
    spec = new_session_spec("C:\\r\\wt-a", "feat-a")
    assert wt_command(spec, "claude.exe") == ["wt", "-w", "new", "nt", "-d", "C:\\r\\wt-a", "--title", "feat-a",
                                              "powershell.exe", "-NoLogo", "-NoExit", "-Command", "& 'claude.exe'"]


def test_program_path_quote_is_escaped_for_powershell():
    cmd = wt_command(new_session_spec("C:\\r", "t"), "C:\\it's\\claude.exe")
    assert cmd[-1] == "& 'C:\\it''s\\claude.exe'"


def test_launch_env_drops_parent_claude_session_variables():
    env = {"PATH": "x", "NO_COLOR": "1", "CLAUDECODE": "1", "CLAUDE_CODE_CHILD_SESSION": "1",
           "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_MESSAGING_TOKEN": "t", "CLAUDE_CODE_ENTRYPOINT": "cli",
           "CLAUDE_CODE_GIT_BASH_PATH": "C:\\git\\bash.exe"}
    assert terminal.launch_env(env) == {"PATH": "x", "CLAUDE_CODE_GIT_BASH_PATH": "C:\\git\\bash.exe"}


def test_semicolons_cannot_split_the_wt_command():
    cmd = wt_command(resume_spec("C:\\a;b", "plan; then build", SID), "claude.exe")
    assert cmd[5] == "C:\\a\\;b"
    assert cmd[7] == "plan, then build"


def test_session_id_validation():
    assert is_session_id(SID)
    assert not is_session_id("../../etc")
    assert not is_session_id(SID.upper() + "x")


def test_launch_uses_wt_when_available(monkeypatch):
    calls = []
    monkeypatch.setattr(terminal.shutil, "which", lambda exe: f"C:\\bin\\{exe}.exe")
    monkeypatch.setattr(terminal.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    monkeypatch.setenv("NO_COLOR", "1")
    result = Launcher().launch(new_session_spec("C:\\r", "main"))
    assert result["via"] == "wt" and calls[0][0][0] == "C:\\bin\\wt.exe"
    assert "NO_COLOR" not in calls[0][1]["env"]


def test_launch_falls_back_to_new_console(monkeypatch):
    calls = []
    monkeypatch.setattr(terminal.shutil, "which", lambda exe: None if exe == "wt" else "C:\\bin\\claude.exe")
    monkeypatch.setattr(terminal.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    result = Launcher().launch(new_session_spec("C:\\r", "main"))
    assert result["via"] == "console"
    assert calls[0][0] == ["C:\\bin\\claude.exe"] and calls[0][1]["cwd"] == "C:\\r"


def test_launch_without_claude_raises(monkeypatch):
    monkeypatch.setattr(terminal.shutil, "which", lambda exe: None)
    with pytest.raises(LaunchError, match="claude"):
        Launcher().launch(new_session_spec("C:\\r", "main"))
