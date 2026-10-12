"""Read session title, last prompt and location from Claude Code transcripts."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TAIL_BYTES = 256 * 1024
PROMPT_MAX = 200


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    ai_title: str
    last_prompt: str
    cwd: str
    git_branch: str
    mtime: float


def read_session_info(path: Path, tail_bytes: int = TAIL_BYTES, known_title: str = "") -> SessionInfo:
    """Parse the transcript tail; scan the whole file only for a title or prompt the tail lacks."""
    found = _scan(_tail_lines(path, tail_bytes))
    title = found["ai_title"] or known_title
    prompt = found["last_prompt"]
    if not title or not prompt:
        full = _full_scan(path)
        title = title or full["ai_title"]
        prompt = prompt or full["last_prompt"]
    return SessionInfo(session_id=path.stem, ai_title=title, last_prompt=prompt,
                       cwd=found["cwd"], git_branch=found["git_branch"], mtime=path.stat().st_mtime)


def find_transcripts(projects_dir: Path, max_age_s: float, now: float) -> dict[str, Path]:
    """Map session id to transcript path for top-level files modified within max_age_s."""
    out: dict[str, Path] = {}
    if not projects_dir.is_dir():
        return out
    for project in projects_dir.iterdir():
        if not project.is_dir():
            continue
        for f in project.glob("*.jsonl"):
            try:
                if now - f.stat().st_mtime <= max_age_s:
                    out[f.stem] = f
            except OSError:
                continue
    return out


def find_transcript(projects_dir: Path, session_id: str) -> Path | None:
    """Find a session's transcript in any project folder, whatever its age."""
    if not projects_dir.is_dir():
        return None
    return next(iter(projects_dir.glob(f"*/{session_id}.jsonl")), None)


class TranscriptCache:
    """Cache parsed transcripts by file size and mtime, and paths by session id."""

    def __init__(self) -> None:
        self._infos: dict[Path, tuple[tuple[int, int], SessionInfo]] = {}
        self._paths: dict[str, Path] = {}

    def get(self, path: Path) -> SessionInfo:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = self._infos.get(path)
        if hit and hit[0] == key:
            return hit[1]
        info = read_session_info(path, known_title=hit[1].ai_title if hit else "")
        self._infos[path] = (key, info)
        return info

    def locate(self, projects_dir: Path, session_id: str) -> Path | None:
        path = self._paths.get(session_id)
        if path is None or not path.exists():
            path = find_transcript(projects_dir, session_id)
            if path is not None:
                self._paths[session_id] = path
        return path


def _tail_lines(path: Path, tail_bytes: int) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > tail_bytes:
            f.seek(size - tail_bytes)
            f.readline()  # drop the partial first line
        data = f.read()
    return data.decode("utf-8", errors="replace").splitlines()


def _scan(lines) -> dict[str, str]:
    out = {"ai_title": "", "last_prompt": "", "cwd": "", "git_branch": ""}
    for line in lines:
        if '"ai-title"' not in line and '"cwd"' not in line and '"type":"user"' not in line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "ai-title" and obj.get("aiTitle"):
            out["ai_title"] = str(obj["aiTitle"])
        if obj.get("cwd"):
            out["cwd"] = str(obj["cwd"])
            out["git_branch"] = str(obj.get("gitBranch") or "")
        if obj.get("type") == "user" and not obj.get("isMeta"):
            prompt = _prompt_text(obj.get("message"))
            if prompt:
                out["last_prompt"] = prompt
    return out


def _full_scan(path: Path) -> dict[str, str]:
    """Scan every line, but parse only title and user lines (cheap substring filter)."""
    with path.open(encoding="utf-8", errors="replace") as f:
        return _scan(line for line in f if '"ai-title"' in line or '"type":"user"' in line)


def _prompt_text(message) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return ""
        content = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return ""
    text = " ".join(content.split())
    if not text or text.startswith("<"):
        return ""
    return text[:PROMPT_MAX]
