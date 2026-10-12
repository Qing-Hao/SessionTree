import json
import os
import time

from session_tree.transcripts import (TranscriptCache, find_transcript, find_transcripts,
                                      read_session_info)


def line(**obj):
    return json.dumps(obj, separators=(",", ":"))


def write(path, *lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_latest_title_and_real_prompt(tmp_path):
    f = tmp_path / "abc.jsonl"
    write(f,
          line(type="ai-title", aiTitle="Old title", sessionId="abc"),
          line(type="user", cwd="C:\\repo", gitBranch="main", message={"role": "user", "content": "fix the login bug"}),
          line(type="ai-title", aiTitle="New title", sessionId="abc"),
          line(type="user", cwd="C:\\repo", gitBranch="feat", message={"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}),
          line(type="user", cwd="C:\\repo", gitBranch="feat", message={"role": "user", "content": "<command-name>/clear</command-name>"}))
    info = read_session_info(f)
    assert info.session_id == "abc"
    assert info.ai_title == "New title"
    assert info.last_prompt == "fix the login bug"
    assert info.cwd == "C:\\repo" and info.git_branch == "feat"


def test_prompt_from_text_blocks_and_truncation(tmp_path):
    f = tmp_path / "s.jsonl"
    write(f, line(type="user", cwd="C:\\r", message={"content": [{"type": "text", "text": "look  at\nthis " + "x" * 300}, {"type": "image"}]}))
    info = read_session_info(f)
    assert info.last_prompt.startswith("look at this x")
    assert len(info.last_prompt) == 200


def test_title_before_tail_window_is_found(tmp_path):
    f = tmp_path / "s.jsonl"
    filler = [line(type="assistant", cwd="C:\\r", message={"content": "x" * 500}) for _ in range(50)]
    write(f, line(type="ai-title", aiTitle="Early title"), *filler,
          line(type="user", cwd="C:\\r", gitBranch="b", message={"content": "hello"}))
    info = read_session_info(f, tail_bytes=2048)
    assert info.ai_title == "Early title"
    assert info.last_prompt == "hello"


def test_prompt_before_tail_window_is_found(tmp_path):
    f = tmp_path / "s.jsonl"
    filler = [line(type="assistant", cwd="C:\\r", message={"content": "x" * 500}) for _ in range(50)]
    write(f, line(type="user", cwd="C:\\r", message={"content": "first"}),
          line(type="user", cwd="C:\\r", message={"content": "go ahead"}),
          line(type="user", cwd="C:\\r", message={"content": [{"type": "tool_result", "content": "big"}]}), *filler)
    assert read_session_info(f, tail_bytes=2048).last_prompt == "go ahead"


def test_known_title_skips_full_scan(tmp_path):
    f = tmp_path / "s.jsonl"
    write(f, line(type="ai-title", aiTitle="Early title"), *[line(type="assistant", cwd="C:\\r") for _ in range(100)])
    assert read_session_info(f, tail_bytes=512, known_title="Cached").ai_title == "Cached"


def test_find_transcripts_recent_only_and_skips_subagents(tmp_path):
    write(tmp_path / "p1" / "a.jsonl", line(type="user"))
    write(tmp_path / "p1" / "b.jsonl", line(type="user"))
    write(tmp_path / "p1" / "a" / "subagents" / "x.jsonl", line(type="user"))
    old = time.time() - 3 * 86400
    os.utime(tmp_path / "p1" / "b.jsonl", (old, old))
    assert set(find_transcripts(tmp_path, 86400, time.time())) == {"a"}
    assert find_transcript(tmp_path, "b") == tmp_path / "p1" / "b.jsonl"
    assert find_transcript(tmp_path, "zzz") is None


def test_cache_rereads_only_when_file_changes(tmp_path):
    f = tmp_path / "s.jsonl"
    write(f, line(type="ai-title", aiTitle="One"))
    cache = TranscriptCache()
    assert cache.get(f).ai_title == "One"
    write(f, line(type="ai-title", aiTitle="Two"), line(type="user", cwd="C:\\r", message={"content": "hi"}))
    assert cache.get(f).ai_title == "Two"


def test_cache_locates_transcript_by_session_id(tmp_path):
    write(tmp_path / "proj" / "sid-1.jsonl", line(type="user"))
    cache = TranscriptCache()
    assert cache.locate(tmp_path, "sid-1") == tmp_path / "proj" / "sid-1.jsonl"
    assert cache.locate(tmp_path, "sid-2") is None
