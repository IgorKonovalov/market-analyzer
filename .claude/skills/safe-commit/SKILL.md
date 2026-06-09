---
name: safe-commit
description: Stage and commit ONLY the files you changed, safely, in this repo. Use whenever you are about to commit work — especially after implementing a plan phase or a bug fix. Enforces explicit-path staging (never `git add -A`/`.`), commits the message inline via the PowerShell tool's here-string (no message files, no `.git` access), runs the project quality gates first, and the no-history-rewrite rule. Trigger on "commit this", "commit the phase", "stage and commit", or any moment a commit is imminent.
---

# safe-commit — market-analyser

A small, strict commit ceremony for this repo. It gets **your own** changes committed
cleanly — without sweeping in stray files or mangling the message. It does **not**
decide *what* to commit or write the design — it just commits.

Two facts shape it:

- **Explicit-path staging.** A `PreToolUse` hook (`block-broad-git-add.js`) hard-blocks
  `git add -A`/`--all`/`.`/`:/`. Stray untracked files (and the occasional parallel
  session) must never get swept in. Stage by named path, always.
- **Message via the PowerShell tool.** The Bash tool truncates PowerShell here-strings
  (`@'...'@`) and strays `@`/backticks into commit subjects. The PowerShell tool handles
  a single-quoted here-string cleanly — so commit the message inline from there. No
  message files, no `.git/` access, no cleanup.

## The ceremony

### 1. See what's there — and what's yours
Run `git status` and `git --no-pager diff --stat`. Identify the files **this session**
created or modified. If you see in-progress files you don't recognise (rare, but parallel
sessions still happen), **leave them alone** — never stage, stash, move, or `git checkout`
them. When in doubt, ask.

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

**Never** `git add -A`, `git add .`, `git add --all`, or `git add :/` — the hook will deny
them, and they'd sweep an untracked or parallel-session file into your commit. Then re-run
`git status` and confirm the **staged** set is exactly your files and nothing else.

### 4. Commit the message inline via the PowerShell tool
Don't write a message file. Don't commit from the Bash tool — it mangles the here-string.
Use the **PowerShell tool** with a single-quoted here-string so nothing in the message is
expanded or truncated:

```powershell
git commit -m @'
feat(data): fetch Yahoo OHLCV by absolute period1/period2

Body explaining the why, wrapped at ~72 cols. Reference the plan/ADR.
'@
```

Here-string rules:
- Use `@'...'@` (single-quoted, literal), **not** `@"..."@` — `$` and backticks stay literal.
- The closing `'@` must be at **column 0** (no indentation) on its own line, or it's a parse error.
- It's one bare `git commit` — no `&&`, no `cd` prefix. (The repo's backdate hook wraps `git commit` automatically; don't add date env vars yourself.)

Conventional-commit format: `type(scope): subject`, blank line, then the body.

### 5. Verify, then stop
Run `git --no-pager show --stat --oneline HEAD` and confirm the commit contains exactly
your files with the intended message. **Do not push** — the user pushes.

## Hard rules (non-negotiable)
- **Never** `git add -A` / `.` / `--all` / `:/`. Explicit paths only.
- **Never** commit from the Bash tool — it mangles here-strings. PowerShell tool only.
- **Never** rewrite history: no `git commit --amend`, no rebase, no reset, no force-push — even for a cosmetic typo in an unpushed commit. Fix mistakes *forward* with a new commit.
- **Never** touch another session's uncommitted/untracked files (no stage, stash, move, or checkout). Surface and wait.
- **Never** push.
- One logical change (or one plan phase) per commit.
