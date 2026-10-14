# Session Tree: design spec

Date: 2026-10-12
Status: draft, waiting for review
Mockup: https://claude.ai/artifact/CjUJjfaHi16YLEsz5MqBsk

## 1. Problem

Running many agent sessions at once, across several repos, worktrees and branches, causes heavy context switching. There is no single view that answers:

- How many sessions are running right now, and which of them need me?
- Which repo, worktree and branch is each session working in?
- What is each session working on?
- Are two sessions editing the same worktree?
- How do I get back to a session quickly?

Research on 2026-10-12 found many launchers (Conductor, Claude Squad, Superset, Nimbalyst, Vibe Kanban, Claude Code Desktop, `claude agents`) and a few observers (claude-view, Eyes on Claude Code, ccboard). None combines a git-graph view of local worktrees with live status for every CLI session on Windows plus one-click return to the terminal.

## 2. Goal and scope

Session Tree is a local web dashboard that wraps the Claude Code CLI. It does not replace the CLI or add a chat UI.

### In scope (v1)

- Claude Code CLI sessions on Windows, started in Windows Terminal.
- Live view of every running session, plus sessions closed in the last 24 hours.
- One card per repo showing a git graph of the local worktree branches, with sessions attached to the worktree they run in.
- Actions:
  - **Go to terminal**: switch to the Windows Terminal tab already running a live session.
  - **Resume**: open a closed session in a new Windows Terminal window.
  - **New session**: start `claude` in a chosen worktree.
  - **Open a second copy** of a live session, behind a warning.

### Out of scope (v1)

- Codex CLI and other agents. Planned for v2, once v1 is stable.
- Terminals embedded in the page. Planned as a later enhancement.
- Creating or deleting worktrees and branches.
- Sessions in the VS Code terminal, Claude Code Desktop, or cloud and remote sessions.
- macOS and Linux.

## 3. Decisions made during brainstorming

| Topic | Decision |
|---|---|
| Form factor | Local web dashboard served from `localhost` |
| Opening sessions | New Windows Terminal window (`wt -w new`) running the real CLI |
| Going to a live session | Focus its existing tab by title (proven by spike, see 6.4) |
| Agents | Claude Code only in v1; Codex CLI in v2 |
| Live status | Read Claude Code's own session registry; no hooks needed |
| Layout | One-screen board of repo cards in columns |
| Branch view | Git graph of branches that have a worktree; base branches without a worktree appear only as dashed "ghost" nodes when something forked from them; other branches stay in a collapsed list |
| Card title | Style B: grey band with divider, 17px bold name, state dot, path on a second line |
| Session text | `aiTitle` as the main line, last prompt as the second line |

## 4. Data sources

All sources are local files and local commands. All are read-only except the terminal actions. Everything below was checked on this machine on 2026-10-12.

### 4.1 Session registry: `~/.claude/sessions/<pid>.json`

Claude Code writes one file per running process. Fields used:

| Field | Example | Use |
|---|---|---|
| `pid` | `10700` | Liveness check |
| `procStart` | `"134359513136022479"` | Windows FILETIME of process start; guards against PID reuse |
| `sessionId` | `28c2caf5-ee1f-…` | Join key to the transcript |
| `cwd` | `C:\Users\…\Agent-Platform` | Maps the session to a worktree |
| `name` | `agent-platform-71` | Secondary label |
| `status` | `idle` / `busy` | Your turn / Working |
| `statusUpdatedAt` | epoch ms | "waiting 8 m" |
| `startedAt` | epoch ms | Uptime |
| `kind`, `entrypoint` | `interactive`, `cli` | Only `interactive` + `cli` sessions get "Go to terminal" |

Files with a `.key` suffix in the same folder are ignored and never read.

**Risk:** this format is undocumented. If it changes, the fallback is a `SessionStart` hook that writes `{session_id, pid, cwd}` to our own folder (see 10).

### 4.2 Transcripts: `~/.claude/projects/<project>/<sessionId>.jsonl`

| Entry | Use |
|---|---|
| `{"type":"ai-title","aiTitle":…}` (last one) | Session title; also the Windows Terminal tab title |
| Last `type:"user"` entry whose `message.content` is a string not starting with `<` | Last prompt |
| `cwd`, `gitBranch` on any entry (last one) | Location for closed sessions; branch the session ran on |
| File modified time | Closed time, for the 24-hour window |

Files under `subagents` folders are skipped. Large files are read from the end (the last 256 KB) to find recent entries quickly. Line-by-line parsing in PowerShell took over 120 seconds for 14 files during research, so the parser must be streaming and tail-first.

### 4.3 Git

Per repo, run with `git -C <path>`:

- `rev-parse --show-toplevel` and `rev-parse --git-common-dir`: find the repo a folder belongs to. Linked worktrees in sibling folders, such as `Agent-Platform-metadata`, share the main repo's common dir and are grouped into one card.
- `worktree list --porcelain`: worktree path, HEAD sha, branch or detached.
- `for-each-ref refs/heads` and `refs/remotes`: local branches (for the collapsed "other branches" list and base detection) and remote-tracking bases such as `origin/Dev`.
- `for-each-ref --merged=<base>`: branches already inside a base.
- `for-each-ref --format=%(ahead-behind:<sha>)` (git 2.41 or later): ahead/behind counts against every candidate in one call. Older git falls back to `rev-list --left-right --count` per pair (see 5.2).
- `reflog show refs/heads/<branch>`: only to settle a parent loop between sibling branches.

## 5. Model

### 5.1 Tree assembly

```
Board
└─ Repo (grouped by git common dir; folders outside git form their own card)
   ├─ Node (one per worktree, plus ghost nodes for base branches)
   │   ├─ branch | detached sha, HEAD sha, path, is_main_checkout
   │   ├─ parent node id, ahead, behind
   │   └─ Sessions (live and closed in the last 24 h)
   └─ loose_branches (local branches with no worktree and no children)
```

- **Session to node:** the node whose worktree path is the longest prefix of the session `cwd`, compared case-insensitively.
- **Shared worktree:** a node with two or more live sessions.
- **Branch switched:** a session's last transcript `gitBranch` differs from the node's current branch.
- **Repos shown:** every repo with at least one live session or one session closed in the last 24 hours. An optional `config.toml` can pin extra repos.

### 5.2 Fork inference

Git does not record which branch a branch was created from, so the parent is inferred. Refs considered:

- every worktree's branch (or sha if detached);
- bases: local branches from a fixed list (`main`, `master`, `Dev`, `dev`, `develop`, `Staging`, `staging`) and their remote-tracking copies such as `origin/Dev`, so a stale local base does not hide where newer branches came from;
- local branches without a worktree, unless already merged into a base. A merged branch can never beat the base that holds it, so leaving it out changes no result and saves git calls.

For each worktree ref `B` that is not a base:

1. For each other ref `P`: `dist` = commits in `B` not in `P`, `behind` = commits in `P` not in `B`. When `dist` equals all of `B`'s commits, the histories are unrelated and `P` is skipped.
2. Skip `P` when `dist == 0 and behind > 0`, because then `P` forked from `B`, not the other way round.
3. Skip `P` when `dist == 0 and behind == 0` and `P` is not a base. Two branches at the same commit must not point at each other.
4. The parent is the remaining `P` with the smallest `dist`. On a tie, prefer a base, in list order, local before remote.
5. `ahead = dist`.

A branch without a worktree that becomes a parent is shown as a ghost node and gets its own parent by the same rules. Bases are roots, and a base without a worktree becomes a ghost node only if some node's parent is that base.

**Parent loops.** Two siblings that split from a commit that is no branch tip pick each other. In a loop, a pick is kept only when the parent's reflog shows it committed the fork commit (it appears in the reflog other than as the creation entry). Every other member re-picks outside the loop. Without reflog evidence, all members re-pick, so siblings from a deleted branch both point at the base.

**Extra labels.** A node with its own commits whose tip is already inside a base shows "merged into <base>". A local base with a remote copy shows how many commits it is behind that copy.

Probe results on this machine (2026-10-12), checked against `git log --graph` and the reflogs:

- Agent-Platform: `origin/Dev → feature/refactor-cleanup (ghost, +7) → feature/live-config (+1, −1)`, `origin/Dev → feature/agent-visualisation (+0)`, `Dev → config/split-metadata-config-version (+5, merged into origin/Dev)`, local `Dev` 22 behind `origin/Dev`.
- AI_Support_Team: `origin/Dev → fix/refactor (ghost, +1) → feature/logfire-instrumentation (+2)`, `Dev → feature/telegram-html-streaming (+6, merged into origin/Dev)`.
- Platform-Integration: main checkout on `Dev` (4 behind `origin/Dev`), `pr-452` under `origin/Dev` (+49), `review-528` and `feature/chat-agent-hierarchical-with-skills-memory` under `pr-530`, the rest under `Dev`.

The first version of these rules (2026-10-12) considered only worktree branches and local bases. It drew Agent-Platform as `Dev → split-metadata (+5) → agent-visualisation (+17) → live-config (+7)`, which the reflogs show was wrong.

**Caching:** git results are cached per repo, keyed by the tuple of worktree HEAD shas. They are recomputed only when a HEAD changes, so the 3-second poll stays cheap.

### 5.3 Session status

| Condition | Shown as |
|---|---|
| Registry file exists, PID alive, process start time within 2 s of `procStart`, `status == busy` | Working |
| Same, `status == idle` | Your turn |
| Same, any other `status` value | The raw value, neutral style |
| Registry entry missing, PID dead or start time mismatch, and transcript modified in the last 24 h | Closed |

`procStart` conversion: `unix_seconds = filetime / 10_000_000 - 11_644_473_600`. For example, `134359513136022479` gives `1791477713.6`, which matches `startedAt` 1791477714865 ms within 1.3 s.

## 6. Components

Proposed layout. This is a new project, so these are all new files:

```
git_visualisation/
  pyproject.toml            # fastapi, uvicorn, psutil; dev: pytest, httpx
  session_tree/
    __init__.py
    registry.py             # read ~/.claude/sessions, liveness check
    transcripts.py          # tail-read jsonl: aiTitle, last prompt, cwd, gitBranch
    git_graph.py            # worktrees, repo grouping, fork inference, cache
    board.py                # assemble Board from the three sources
    terminal.py             # wt launch commands, focus-tab call
    focus_tab.ps1           # UI Automation helper, from the spike
    server.py               # FastAPI app and routes
    static/index.html       # dashboard page, from the mockup
  tests/
  docs/superpowers/specs/
```

### 6.1 `registry.py`

`read_registry(dir) -> list[RegistryEntry]`. Parses each `<pid>.json`, skips malformed files and records a warning, and runs the liveness check with `psutil.Process(pid).create_time()`.

### 6.2 `transcripts.py`

`read_session_info(path) -> SessionInfo(ai_title, last_prompt, cwd, git_branch, mtime)`. Tail-first read; prompts are truncated to 200 characters.

`recent_closed(projects_dir, live_ids, hours=24)` lists closed sessions.

### 6.3 `git_graph.py`

`repo_key(path)` returns the common dir. `read_repo(root) -> RepoGraph` implements 4.3 and 5.2, and is cached by HEAD shas. Each git call has a 5-second timeout. A timeout or a missing folder marks the repo `error`; it does not fail the whole board.

### 6.4 `terminal.py` and `focus_tab.ps1`

Commands are built as argument lists, never shell strings.

- **Resume:** `["wt", "-w", "new", "nt", "-d", <cwd>, "--title", <aiTitle or name>, "claude", "--resume", <sessionId>]`
- **New session:** `["wt", "-w", "new", "nt", "-d", <worktree path>, "--title", <branch>, "claude"]`
- **Go to terminal:** runs `powershell -NoProfile -File focus_tab.ps1 -Target <aiTitle>`. The script:
  1. Finds Windows Terminal windows (`CASCADIA_HOSTING_WINDOW_CLASS`).
  2. Finds a `TabItem` whose name, after stripping leading non-letter and non-digit characters such as `✳` or `◐`, equals the target.
  3. Selects the tab with `SelectionItemPattern.Select()`.
  4. Restores the window if minimised.
  5. Brings it to the front with `SetForegroundWindow`, falling back to a synthetic Alt key press plus `SetForegroundWindow`, then to UIA `SetFocus`.

  The script prints JSON: `{result: ok|not_found|ambiguous|no_foreground, method, tabs[]}`.

  - `not_found` or `ambiguous`: the page shows the list of open tab titles to pick from, then calls focus again with the exact title.
  - `no_foreground`: the page says the tab was selected but Windows blocked bringing it to the front, so check the flashing taskbar icon.

The spike on 2026-10-12 switched successfully from a foreground terminal both times, using `SetForegroundWindow` directly. Calls from the background server and switching between tabs inside a multi-tab window are **not yet proven**; see 9.2.

### 6.5 `server.py`

- `GET /` serves `static/index.html`.
- `GET /api/board` returns the full board as JSON: repos, nodes, sessions, alerts, warnings, `generated_at`.
- `POST /api/sessions/{id}/focus`, `POST /api/sessions/{id}/resume`, and `POST /api/worktrees/new` with body `{path}`.

Rules:

- Binds to `127.0.0.1` only. Default port 4380 (4317 is the OpenTelemetry gRPC port); set it with `--port`.
- POST requests must carry the header `X-Session-Tree: 1` and an `Origin` matching the server. Otherwise the server returns 403. This stops other websites from triggering terminal launches through the browser.
- Session ids must match the UUID format and exist in the current board. Worktree paths must exactly match a known node path. Unknown values return 404. No user input ever reaches a command except as a known value.
- Start with `python -m session_tree` (`--open` opens the browser).

### 6.6 Frontend `static/index.html`

Built from the approved mockup. It is plain HTML, CSS and JS with no build step. The two web fonts are bundled locally so the page works offline, with system fonts as fallback.

- **Header strip:** title, live count, filter chips (All, Your turn, Working, Closed 24 h, Shared worktree), and a freshness note showing the last update time.
- **Alerts strip:** shared worktrees, sessions waiting 2 hours or more, and branch switches. Clicking an alert scrolls to and highlights the card.
- **Board:** CSS columns (`columns: 4 380px`). One card per repo, in this order: has Working, has shared, has Your turn, has Closed only; then by name.
- **Card head (style B):** state dot, repo name, counts, and the path on a second line.
- **Graph:** rows indented by fork depth. SVG fork lines are drawn after layout and redrawn on resize. Each row shows the branch (or `<folder> detached @ sha`), `+ahead −behind` (behind shown orange when over 50), the worktree folder or "main checkout", and a `+ session` button. Ghost nodes are dashed.
- **Session line:**
  - Status pill, `aiTitle`, and wait or closed time.
  - Second line: session name and last prompt.
  - Inline **Go to terminal** for live sessions, **Resume** for closed ones.
  - Clicking the line expands the detail: full title, last prompt, PID, uptime, path, and "Open a second copy" behind a warning.
- **Behaviour:**
  - Filters dim non-matching items instead of hiding them.
  - Polls `GET /api/board` every 3 seconds; skips a poll while the previous one is still pending.
  - Keeps the expanded session and the filter across refreshes.
- **Errors:** if the server is unreachable, a banner says "Session Tree server is not running. Start it with `python -m session_tree`." Other failures appear as a message on the affected card.

## 7. Error handling

| Failure | Behaviour |
|---|---|
| Malformed registry or transcript file | Skip it; add to `warnings`; the page shows a count in the footer |
| Repo folder deleted or git error or timeout | Card shows "Cannot read git data: <reason>"; its sessions are still listed |
| `wt` not found | Fall back to `cmd /c start "" /D <cwd> claude …`; the page notes it |
| `claude` not on PATH | Action returns an error naming the missing command |
| Focus fails | See 6.4 results |
| Registry folder missing | Board shows an empty state explaining that no Claude Code sessions were found |

## 8. Privacy

All data stays on the machine. The server makes no outbound requests. Prompts can contain secrets (a login link was seen during research), so the server binds to localhost only and prompts are never logged.

## 9. Testing and evidence

### 9.1 Automated tests (pytest)

| Test | Input | Expected output |
|---|---|---|
| FILETIME conversion | `134359513136022479` | `1791477713.60` seconds (±0.01) |
| Live entry | Registry fixture with the current test process PID and its real start time | status from file, `live=True` |
| PID reuse | Same PID, `procStart` off by 1 hour | `live=False` |
| Dead PID | PID of an exited child process | `live=False` |
| Malformed registry | `{"pid":` | skipped, 1 warning |
| Transcript title | Fixture with two `ai-title` lines | latest title |
| Last prompt | Fixture with a tool-result user entry and a `<command>` entry after a real prompt | the real prompt |
| Repo grouping | Temp repo plus a linked worktree in a sibling folder | one repo, two nodes |
| Fork chain | Temp repo: `main` → `feat-a` (+2) → `feat-b` (+3), worktrees for both branches | `feat-a.parent=main` (ghost), `feat-b.parent=feat-a`, ahead 2 and 3 |
| Twins | Two worktree branches at the same commit off `main` | both have parent `main` |
| Detached worktree | `worktree add --detach` | node labelled by folder, `detached=True` |
| Session mapping | cwd inside a linked worktree | mapped to that node, not the main checkout |
| Shared | Two live sessions with the same cwd | `shared=True`, one alert |
| Resume command | Known session | exact argument list from 6.4 |
| Unknown session | Random UUID | 404, no process started |
| CSRF guard | POST without header, or with a foreign Origin | 403 |

Process launches are mocked in tests; no real terminals are opened.

### 9.2 Live evidence (manual, shown to you before calling v1 done)

1. **Count matches:** the board's live count equals the number of `claude.exe` processes with a registry file (screenshot plus `Get-Process claude` output).
2. **Graph matches the probe:** the Agent-Platform card shows `Dev → split-metadata (+5) → agent-visualisation (+17) → live-config (+7)`.
3. **Close detection:** close one terminal tab; within one poll (3 seconds or less) that session turns Closed (before and after screenshots).
4. **Resume:** click Resume; a new Windows Terminal window opens in the right folder with the conversation restored.
5. **Go to terminal from the browser:** click it on a session whose window is behind the browser; that tab comes to the front. Record which method worked (A, B or C). Repeat for a tab inside the 2-tab window.
6. **New session:** click `+ session` on a worktree; `claude` starts in that folder.

## 10. Risks and fallbacks

| Risk | Fallback |
|---|---|
| Registry format changes in a Claude Code update | `SessionStart` hook writing our own registry; same liveness check |
| Windows blocks foreground from the background server | Alt key trick, then UIA `SetFocus`, then "check taskbar" message |
| Tab renamed, or titles turned off in Claude Code | Pick-from-list fallback |
| Fork inference wrong after a rebase or merge | Footer note on the page; v2 could add a manual parent override |
| Large transcripts slow the poll | Tail-first reads and caching by file modified time |

## 11. Later versions

- v2: Codex CLI sessions (`~/.codex/sessions/*.jsonl`, `codex resume <id>`), once a liveness signal is found.
- Terminal embedded in the page (xterm.js + node-pty or a Python equivalent).
- Creating a worktree from a branch in the collapsed list.
- Windows notifications when a session switches to Your turn.
