# Session Tree

A local dashboard for people who run several Claude Code CLI sessions at once. It shows every repo you are working in as a card. Each card holds a git graph of your worktrees, showing which branch forked from which, with the live and recently closed Claude Code sessions attached to the branch they run on. From a card you can jump back to a session's terminal tab, resume a closed session, start a new one, or open the worktree in VS Code.

It runs in the system tray, starts when you sign in, and opens in its own window at http://127.0.0.1:4317.

## Current scope

The tool is in development and shared for team trial. For now it supports only this setup:

- **Windows** (Windows 11). No macOS or Linux.
- **Claude Code CLI** sessions. Not the Claude desktop app, Codex CLI or other agents.
- **VS Code** as the editor for the **code** button. Not Cursor or other editors.

Support for more platforms, agents or editors may come later; this README will be updated when it does. Please report anything odd (see [Feedback](#feedback)).

## Prerequisites

Install all of these before setup. Versions are the ones tested on 2026-10-12.

| Tool | How to check | Tested with | Used for |
|---|---|---|---|
| Windows 11 | `winver` | 11 Pro, build 26200 | everything |
| [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) on PATH | `claude --version` | 2.1.296 | the sessions it shows, Resume, + session |
| [uv](https://docs.astral.sh/uv/) on PATH | `uv --version` | 0.8.22 | installs Python 3.12+ and the dependencies |
| Git on PATH | `git --version` | 2.51.0 | branch graph (2.41 or later is faster) |
| Windows Terminal | `(Get-AppxPackage Microsoft.WindowsTerminal).Version` | 1.24 | Go to terminal, Resume, + session |
| VS Code, with `code` on PATH | `code --version` | 1.108.0 | the **code** button |
| Microsoft Edge (built into Windows 11) or Google Chrome | — | Edge | the dashboard's own window |

## What you get

- **One card per repo** with a session, sorted so busy repos come first.
- **Branch graph per repo:** worktrees nest under the branch they forked from, with `+ahead −behind` counts. The graph also shows:
  - Grey dashed nodes for branches that have no worktree but are a parent, and for bases such as `Dev` or `origin/Dev`.
  - A "merged into origin/Dev" label on branches that are already merged.
  - An orange "22 behind origin/Dev" warning when your local base is stale.
  - Branches with no worktree and no children, folded into "N other branches".
- **Session status:** Working, Your turn (with how long it has waited), or Closed (kept for 24 hours).
- **Alerts** for two live sessions sharing one worktree, sessions waiting over 2 hours, and sessions whose worktree switched branch.
- **Buttons on each row:**
  - **Go to terminal:** brings the session's existing Windows Terminal tab to the front.
  - **Resume:** reopens a closed session in a new tab.
  - **+ session:** starts a new Claude Code session in that worktree.
  - **code:** opens the worktree in VS Code, the same as `code .` run in that folder.

## Setup

1. **Install uv** if `uv --version` fails. Use either command from the [uv install docs](https://docs.astral.sh/uv/getting-started/installation/):

   ```powershell
   winget install --id=astral-sh.uv -e
   # or
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   Open a new terminal afterwards so `uv` is on PATH.

2. **Clone the repo** into a folder you will keep. The login shortcut points at this folder, so if you move it later, run step 5 again.

   ```powershell
   cd $HOME\Documents
   git clone <repo-url> git_visualisation
   cd git_visualisation
   ```

3. **Install the dependencies.** This creates `.venv` and downloads Python 3.12 if you don't have it.

   ```powershell
   uv sync
   ```

4. **Try it once in the foreground:**

   ```powershell
   uv run python -m session_tree --open
   ```

   Your browser opens the dashboard and you should see a card for each repo with a Claude Code session. Press `Ctrl+C` to stop.

5. **Install it as a tray app that starts at sign-in:**

   ```powershell
   uv run python -m session_tree --install
   ```

   This adds a **Session Tree** shortcut to your Startup folder and the Start menu, then starts the tray app. Windows 11 puts new tray icons behind the **^** arrow on the taskbar. To keep it visible, drag the icon onto the taskbar, or turn it on under Settings, Personalization, Taskbar, Other system tray icons.

## Daily use

- **Click the tray icon**, or open **Session Tree** from the Start menu, to open the dashboard window. Starting it a second time doesn't run a second copy; it just opens the window.
- **Right-click the tray icon** for **Open dashboard**, **Restart** and **Quit**.
- The page refreshes every 3 seconds. Hover over a `+ahead −behind` count to see which branch it is measured against.

## Updating

```powershell
cd <your clone>
git pull
uv sync
```

Then right-click the tray icon and choose **Restart** to load the new code.

## Uninstall

```powershell
uv run python -m session_tree --uninstall   # removes the Startup and Start menu shortcuts
```

Quit the tray app from its menu, then delete the clone and `%USERPROFILE%\.session-tree` if you want everything gone.

## Configuration

Optional file: `%USERPROFILE%\.session-tree\config.toml`.

```toml
# Always show these repos, even with no Claude Code session in them.
pinned_repos = ['C:\Users\you\Documents\some-repo']
```

Command line options:

| Option | Meaning |
|---|---|
| `--port N` | serve on another port (default 4317) |
| `--open` | foreground mode, opens the browser |
| `--tray` | background mode with the tray icon (what the shortcut runs) |
| `--install` / `--uninstall` | add or remove the login and Start menu shortcuts |
| `--claude-dir PATH` | read sessions from another Claude config folder (default `%USERPROFILE%\.claude`) |

## How the branch graph is worked out

Git does not record which branch a branch was created from, so Session Tree infers it:

- **Nearest fork point wins.** A branch's parent is the candidate it shares the most recent commit with. Candidates are:
  - the other worktree branches;
  - base branches (`main`, `master`, `Dev`, `dev`, `develop`, `Staging`, `staging`) and their remote copies such as `origin/Dev`;
  - unmerged branches without a worktree.
- **Ties go to the base:** local first, then remote.
- **Siblings that point at each other:** when two branches carry the same commits, the reflog decides which one made those commits.

The graph is only as fresh as your remote-tracking refs, so run `git fetch` if `origin/Dev` looks old. Full rules are in [the design spec](docs/superpowers/specs/2026-10-12-session-tree-design.md), section 5.2.

## Troubleshooting

| Problem | What to check |
|---|---|
| No tray icon after sign-in | Look behind the **^** arrow. Check that `Session Tree.lnk` is in `shell:startup` (paste that into Win+R). Read the log at `%USERPROFILE%\.session-tree\session-tree.log`. |
| Dashboard won't load | Something else may be using port 4317; `Get-NetTCPConnection -LocalPort 4317` shows it. Run with `--port 4320` instead. |
| A prerequisite command is not found | Open a new terminal after installing it, so PATH reloads. |
| **Go to terminal** says no tab was found | It finds the tab by the session's title. Sessions without a title yet cannot be found; pick the tab from the list it shows, or use **Open a second copy**. |
| **code** says "VS Code (code) is not on PATH" | Rerun the VS Code installer with "Add to PATH" ticked (on by default), or add VS Code's `bin` folder to your user PATH. Then sign out and in, or choose **Restart** from the tray menu, so the tray app sees the new PATH. |
| A repo is missing | Only repos with a live session, or one closed in the last 24 hours, are shown. Add others to `pinned_repos`. |
| A branch's parent looks wrong | Run `git fetch`, then wait for the next refresh. If it is still wrong, send the repo's `git log --graph --oneline --all` (see Feedback). |

## Privacy and security

- The server listens on `127.0.0.1` only and rejects requests whose Host or Origin is not local, so other machines and other websites cannot reach it.
- It reads `%USERPROFILE%\.claude\sessions` and `%USERPROFILE%\.claude\projects` and runs `git` in your repos. Nothing is sent anywhere, and session prompts are never written to the log.
- Session prompts can contain secrets, such as tokens or login links, and the dashboard shows the last prompt of each session. Take care when screen sharing it.

## Development

```powershell
uv sync                 # includes the dev group (pytest, httpx)
uv run pytest -q        # full test suite
uv run python -m session_tree --open   # run from source in the foreground
```

If the tray app is running, quit it first or use another `--port`.

Project layout:

| Path | What it does |
|---|---|
| `session_tree/registry.py` | live sessions from `~/.claude/sessions/<pid>.json`, with PID and start-time checks |
| `session_tree/transcripts.py` | title, last prompt, folder and branch from the session transcripts |
| `session_tree/git_graph.py` | worktrees, branch tips and fork inference |
| `session_tree/board.py` | puts repos, nodes, sessions and alerts together; refreshed every 3 s |
| `session_tree/terminal.py`, `focus_tab.ps1` | opens and focuses Windows Terminal tabs, and opens VS Code |
| `session_tree/server.py` | FastAPI app: the page, `/api/board` and the actions |
| `session_tree/tray.py` | tray icon, single instance check, login shortcuts |
| `session_tree/static/index.html` | the whole frontend, no build step |
| `docs/superpowers/` | design spec and implementation plan |

## Feedback

Send problems and ideas to the maintainer. Include:
- what you clicked and what you expected;
- the last lines of `%USERPROFILE%\.session-tree\session-tree.log`;
- for graph issues, `git log --graph --oneline --all -n 60` from that repo.

Check the log for prompts before sending it.
