# Threat Model

Primary threats considered in the current design:

1. Unauthorized remote control
   - mitigated with sender allowlist checks
2. Path traversal and filesystem abuse
   - mitigated with normalized path checks and root allowlists
3. Unsafe tool execution from model text
   - mitigated with typed tools and explicit executor validation
4. Destructive actions without user approval
   - mitigated with policy decisions and confirmation requirements
5. Log leakage
   - mitigated by structured audit events with optional redaction hooks
6. Remote development abuse
   - mitigated by repo allowlists, command allowlists, and confirmation gates

## Additional Remote Programming Threats

### Threat: Agent escapes the target repository

Risk:

- reads secrets outside the repo
- modifies unrelated files
- touches system directories

Mitigation:

- enforce repo-root path normalization
- deny writes outside active repo root
- require explicit repo selection before repo-dev tools are enabled

### Threat: Model turns a coding task into arbitrary shell execution

Risk:

- remote code execution on the host
- destructive system changes

Mitigation:

- no free-form shell tool
- command registry with named commands only
- no direct shell execution from model text

### Threat: Dangerous git operations damage the repo

Risk:

- history rewrite
- working tree deletion
- silent data loss

Mitigation:

- confirmation for commit/push
- deny or separately gate reset/clean/force operations
- keep audit trail for every repo action

### Threat: Secrets leak through repo logs or chat responses

Risk:

- `.env` values exposed in Telegram
- tokens committed or summarized by the model

Mitigation:

- redact token-like strings from outputs
- ignore `.env`, logs, and runtime state in git
- keep sensitive files out of cloud context by default

The model is advisory only. The executor never converts free-form natural language directly into shell execution.
