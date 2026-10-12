import json
import subprocess
import sys
import time

import psutil
import pytest

from session_tree.registry import FILETIME_EPOCH_OFFSET, filetime_to_unix, read_registry

SID = "11111111-2222-3333-4444-555555555555"


def to_filetime(unix_s: float) -> int:
    return int((unix_s + FILETIME_EPOCH_OFFSET) * 10_000_000)


def write_entry(directory, pid, proc_start_ft, **extra):
    data = {"pid": pid, "sessionId": SID, "cwd": "C:\\x", "name": "x-1", "status": "idle",
            "statusUpdatedAt": 1, "startedAt": 1, "kind": "interactive", "entrypoint": "cli",
            "procStart": str(proc_start_ft), **extra}
    (directory / f"{pid}.json").write_text(json.dumps(data), encoding="utf-8")


def test_filetime_conversion():
    assert filetime_to_unix(134359513136022479) == pytest.approx(1791477713.60, abs=0.01)


def test_live_entry(tmp_path):
    me = psutil.Process()
    write_entry(tmp_path, me.pid, to_filetime(me.create_time()))
    entries, warnings = read_registry(tmp_path)
    assert [e.pid for e in entries] == [me.pid]
    assert entries[0].status == "idle" and entries[0].session_id == SID
    assert warnings == []


def test_pid_reuse_is_not_live(tmp_path):
    me = psutil.Process()
    write_entry(tmp_path, me.pid, to_filetime(me.create_time() - 3600))
    assert read_registry(tmp_path)[0] == []


def test_dead_pid_is_not_live(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    write_entry(tmp_path, child.pid, to_filetime(time.time()))
    assert read_registry(tmp_path)[0] == []


def test_malformed_file_is_skipped_and_key_files_ignored(tmp_path):
    (tmp_path / "123.json").write_text('{"pid":', encoding="utf-8")
    (tmp_path / "123.abc.key").write_text("secret", encoding="utf-8")
    entries, warnings = read_registry(tmp_path)
    assert entries == []
    assert len(warnings) == 1 and "123.json" in warnings[0]


def test_missing_directory(tmp_path):
    assert read_registry(tmp_path / "nope") == ([], [])
