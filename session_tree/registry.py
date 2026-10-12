"""Read Claude Code's per-process session registry and keep only live sessions."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import psutil

FILETIME_EPOCH_OFFSET = 11_644_473_600
START_TOLERANCE_S = 2.0


@dataclass(frozen=True)
class RegistryEntry:
    pid: int
    session_id: str
    cwd: str
    name: str
    status: str
    status_updated_ms: int
    started_ms: int
    kind: str
    entrypoint: str
    proc_start: float | None


def filetime_to_unix(filetime: int) -> float:
    """Convert a Windows FILETIME (100 ns ticks since 1601) to unix seconds."""
    return filetime / 10_000_000 - FILETIME_EPOCH_OFFSET


def is_alive(pid: int, proc_start: float | None) -> bool:
    """True when the PID runs and started at proc_start, which rules out PID reuse."""
    try:
        created = psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False
    return proc_start is None or abs(created - proc_start) <= START_TOLERANCE_S


def read_registry(directory: Path) -> tuple[list[RegistryEntry], list[str]]:
    """Return live registry entries plus a warning per unreadable file."""
    entries: list[RegistryEntry] = []
    warnings: list[str] = []
    if not directory.is_dir():
        return entries, warnings
    for path in sorted(directory.glob("*.json")):
        try:
            entry = _parse(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            warnings.append(f"Skipped {path.name}: {exc}")
            continue
        if is_alive(entry.pid, entry.proc_start):
            entries.append(entry)
    return entries, warnings


def _parse(raw: dict) -> RegistryEntry:
    proc = raw.get("procStart")
    return RegistryEntry(
        pid=int(raw["pid"]),
        session_id=str(raw["sessionId"]),
        cwd=str(raw["cwd"]),
        name=str(raw.get("name") or ""),
        status=str(raw.get("status") or ""),
        status_updated_ms=int(raw.get("statusUpdatedAt") or raw.get("updatedAt") or 0),
        started_ms=int(raw.get("startedAt") or 0),
        kind=str(raw.get("kind") or ""),
        entrypoint=str(raw.get("entrypoint") or ""),
        proc_start=filetime_to_unix(int(proc)) if proc else None,
    )
