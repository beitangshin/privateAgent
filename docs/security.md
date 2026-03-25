# Security

Security rules implemented in the current scaffold:

- allowlisted sender IDs only
- allowlisted chat IDs only when configured
- path normalization before filesystem access
- filesystem reads and writes limited to configured roots
- high-risk categories require confirmation
- side-effect tools can be blocked by safe mode
- every handled request receives a trace ID
- audit events are persisted as JSON lines
- model calls are logged separately

## Repo Development Safety Rules

Remote development support must follow these rules:

- never execute raw shell text from Telegram
- never execute raw shell text from model output
- restrict work to explicitly allowlisted repositories
- bind each session to one active repository
- restrict commands to a named allowlist
- restrict file edits to repo roots
- require confirmation for commits, pushes, deletions, and destructive git actions
- deny system-level paths and privileged commands

## Forbidden by Default

The following remain forbidden unless explicitly and narrowly approved later:

- unrestricted PowerShell
- unrestricted CMD
- unrestricted Bash
- arbitrary Python execution from chat input
- privilege escalation
- credential harvesting
- browser cookie extraction
- startup persistence tricks
- destructive git cleanup commands without confirmation

## Commands That Should Be Denied or Confirmation-Gated

Examples:

- `git reset --hard`
- `git clean -fdx`
- `git push --force`
- `del`, `rm`, `rmdir`
- `shutdown`, `logoff`, `restart-computer`
- `powershell -encodedcommand`
- any command that writes outside the active repository

## Recommended Safe Command Classes

Examples:

- `git status`
- `git diff`
- `git add <allowlisted paths>`
- `python -m pytest`
- `gradlew test`
- `rg`
- `type` / file reads

These should still run through policy and logging.
