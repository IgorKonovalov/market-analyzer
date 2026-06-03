---
name: safe-commit
description: Stage and commit ONLY the files you changed, safely, in this multi-session repo. Use whenever you are about to commit work — especially after implementing a plan phase or a bug fix. Enforces explicit-path staging (never `git add -A`/`.`), a file-based commit message (never PowerShell here-strings in the Bash tool), the project quality gates before committing, and the no-history-rewrite rule. Trigger on "commit this", "commit the phase", "stage and commit", or any moment a commit is imminent.
---

# safe-commit — market-analyser

A small, strict commit ceremony for this repo. It exists because this working tree
is shared by **parallel Claude sessions**, and because the Bash tool mangles
PowerShell here-strings. Both have repeatedly contaminated or corrupted commits.
This skill makes the safe path the default. It does **not** decide *what* to
commit or write the design — it just gets your own changes committed cleanly.

A `PreToolUse` hook (`block-broad-git-add.js`) hard-blocks `git add -A`/`--all`/`.`/`:/`.
This skill is the workflow that keeps you from ever hitting that block.

## The ceremony

### 1. See what's there — and what's yours
Run `git status` and `git --no-pager diff --stat`. In a multi-session repo, not
every dirty file is yours. Identify the files **this session** created or modified.
If you see in-progress files you don't recognise (another session's work), **leave
them alone** — never stage, stash, move, or `git checkout` them. When in doubt, ask.

### 2. Run the gates (before staging)
Run only the gates relevant to what changed:
- **Python** (`src/`, `tests/`): `.venv/Scripts/python.exe -m pytest -m "not network"`, then `.venv/Scripts/python.exe -m mypy --strict src tests`, then `.venv/Scripts/ruff.exe check`.
- **Desktop** (`desktop/`): `pnpm --filter desktop lint`, `pnpm --filter desktop test:main`, `pnpm gen-types:check`, and `pnpm test:e2e` when renderer/IPC behaviour changed.
- **Docs only** (`docs/`, `*.md`): no code gates; a markdown/mermaid check if the plan names one.

All relevant gates must be green **before** you stage. If a gate fails, fix it (or surface it) — do not commit around a red gate.

### 3. Stage by explicit path — never broadly
Stage each file you own, by path:

```
git add src/market_analyser/data/yahoo.py tests/data/test_yahoo.py
```

**Never** `git add -A`, `git add .`, `git add --all`, or `git add :/` — the hook
will deny them, and they'd sweep a parallel session's files into your commit.
Then re-run `git status` and confirm the **staged** set is exactly your files and
nothing else.

### 4. Commit via a message file — never a here-string
The Bash tool truncates/garbles PowerShell here-strings (`@'...'@`) and stray `@`/
backticks have ended up in commit subjects. Sidestep shell quoting entirely:
**write the message to a file with the Write tool**, then commit with `-F`.

1. Write the conventional-commit message to `.git/SAFE_COMMIT_MSG` (untracked, so it can't contaminate anything).
2. `git commit -F .git/SAFE_COMMIT_MSG`

Conventional-commit format, e.g.:

```
feat(data): fetch Yahoo OHLCV by absolute period1/period2

Body explaining the why, wrapped at ~72 cols. Reference the plan/ADR.
```

(The repo's backdate hook wraps `git commit` automatically — don't add date env vars yourself.)

### 5. Verify, then stop
Run `git --no-pager show --stat --oneline HEAD` and confirm the commit contains
exactly your files with the intended message. **Do not push** — the user pushes.

## Hard rules (non-negotiable)
- **Never** `git add -A` / `.` / `--all` / `:/`. Explicit paths only.
- **Never** rewrite history: no `git commit --amend`, no rebase, no reset, no force-push — even for a cosmetic typo in an unpushed commit. Fix mistakes *forward* with a new commit.
- **Never** touch another session's uncommitted/untracked files (no stage, stash, move, or checkout). Surface and wait.
- **Never** push.
- One logical change (or one plan phase) per commit.
