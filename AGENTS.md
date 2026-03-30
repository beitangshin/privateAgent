# AGENTS.md

## Project overview

This project aims to build a **personal remote-control AI assistant** similar in spirit to OpenClaw:

- Use a **public chat application** as the remote control interface.
- Run the **execution core on a desktop computer**.
- Use a **local LLM first**, currently planned as **DeepSeek**.
- Support **tool calling / task dispatching / system operations**.
- Prioritize **security, auditability, bounded permissions, and safe-by-default behavior**.
- Use **Codex** as the engineering copilot for implementation.

This file defines the engineering rules, architecture constraints, coding standards, and delivery principles for all agents working in this repository.

---

## Primary goal

Build a secure local-first assistant that can:

1. Receive instructions from an approved remote chat channel.
2. Parse intent and classify risk.
3. Decide whether the request can be handled automatically.
4. Execute actions on the desktop through well-defined tools.
5. Return structured results, logs, and failure reasons.
6. Refuse, defer, or request confirmation when safety conditions are not met.

---

## Non-goals

Unless explicitly requested later, do **not** optimize for:

- fully autonomous self-directed behavior
- unrestricted shell access from remote chat
- stealth execution or evasion features
- privilege escalation
- persistence mechanisms outside normal app startup configuration
- hidden background behavior that the user cannot inspect
- multi-user SaaS architecture

This is a **personal assistant system**, not a malware framework, not a red-team implant, and not an uncontrolled general agent.

---

## Product principles

All agents must follow these principles:

### 1. Local-first
- Prefer local model inference and local tool execution.
- Cloud dependency should be optional, isolated, and easy to disable.
- Sensitive data should remain local by default.

### 2. Safe-by-default
- The default configuration must be restrictive.
- Every tool must have explicit allow/deny boundaries.
- High-risk operations must require confirmation or be blocked.

### 3. Observable
- Every action must leave a readable audit trail.
- Inputs, decisions, tool calls, outputs, and failures must be logged.
- Logs must be redactable and rotation-friendly.

### 4. Modular
- Chat transport, agent core, model backend, tools, policy engine, and memory must be separable.
- Swapping Telegram/Discord/Matrix or DeepSeek/Ollama/vLLM should require minimal code changes.

### 5. Deterministic execution boundary
- The model may suggest actions.
- The executor performs actions only through typed, validated tools.
- Never let model text directly become shell execution.

### 6. Human override first
- The user must be able to pause, disable, or restrict the system easily.
- A kill switch must exist.

---

## Initial target architecture

Use the following baseline architecture.

```text
Remote Chat App
    -> Transport Adapter
    -> Auth / Sender Verification
    -> Command Router
    -> Policy Engine
    -> Planner / Agent Core
    -> Local Model Backend (DeepSeek via Ollama/vLLM/LM Studio/etc.)
    -> Tool Runtime
         -> Desktop Control Tools
         -> File Tools
         -> Shell Tools (restricted)
         -> Web / Search Tools (optional)
         -> Automation / Scheduling Tools
    -> Audit Logger
    -> State Store
```

### Recommended top-level modules

```text
/src
  /transport        # Telegram/Discord/Matrix/other chat adapters
  /auth             # sender verification, secrets, session rules
  /agent            # planner, memory, orchestration, response formatting
  /policy           # risk classification, permission checks, confirmations
  /models           # DeepSeek backend abstraction, prompt handling
  /tools            # typed tool definitions and implementations
  /executor         # safe tool invocation runtime
  /audit            # logs, trace ids, event records
  /storage          # sqlite/json config/state/job storage
  /scheduler        # timed jobs and recurring tasks
  /config           # config parsing and defaults
  /ui               # optional local admin UI
/tests
/docs
/scripts
```

---

## Suggested implementation path

Agents should implement in phases.

### Phase 1: Minimal secure remote command loop
Target: a usable but narrow MVP.

Must include:
- one chat transport adapter
- one local model backend
- one policy engine
- a small typed tool set
- audit logging
- allowlisted user identity
- confirmation flow for dangerous actions

MVP tool examples:
- `ping`
- `summarize_desktop_status`
- `read_allowed_file`
- `list_allowed_directory`
- `run_preapproved_task`
- `capture_system_info`
- `take_note`

Do **not** include arbitrary shell execution in the first phase.

### Phase 2: Controlled automation
Add:
- background job queue
- scheduler
- structured task results
- file generation
- notification callbacks
- optional web/search tools

### Phase 3: Advanced desktop actions
Add only with explicit policy controls:
- window automation
- browser automation
- clipboard access
- local app launching
- richer file workflows

---

## Security requirements

Security is a core requirement, not a later enhancement.

### Identity and transport security
- Only accept messages from explicitly allowlisted user IDs.
- Validate sender identity using platform-native identifiers, not display names.
- Secrets must come from environment variables or secret files excluded from git.
- Do not hardcode API keys, chat tokens, or admin IDs.
- Support message signing or shared-secret verification when possible.

### Host security
- Run the service as a **non-admin/non-root user** whenever possible.
- Restrict filesystem access to configured directories.
- Restrict tool access using explicit allowlists.
- Never expose unrestricted shell, PowerShell, AppleScript, or SSH passthrough from raw model output.
- Support a global `safe_mode` that disables high-risk tools.

### Execution safety
- Every tool must declare:
  - name
  - description
  - input schema
  - risk level
  - side-effect flag
  - confirmation requirement
  - timeout
  - allowlist/denylist constraints
- All tool inputs must be schema-validated before execution.
- All executions must use timeouts.
- All executions must produce structured results.
- High-risk tools must require a second explicit confirmation step from the user.

### Audit and traceability
- Every incoming request gets a trace ID.
- Persist:
  - timestamp
  - sender ID
  - original message
  - parsed intent
  - chosen tools
  - arguments
  - policy decisions
  - result summary
  - error details
- Sensitive values should be redacted in logs when practical.

### Kill switch and emergency controls
The system must support:
- immediate global disable flag
- transport disconnect mode
- tool-category disable flags
- emergency confirmation-only mode

---

## Threat model

Agents should design against the following threats.

### Threat 1: Unauthorized remote control
Mitigation:
- strict sender allowlist
- secret management
- optional 2-step confirmation for privileged actions
- replay protection where applicable

### Threat 2: Prompt injection through chat or web content
Mitigation:
- treat all untrusted content as data, not instructions
- separate system policy from model-visible user content where possible
- do not allow tool execution from raw pasted instructions without policy checks
- never trust tool-returned text as executable instructions

### Threat 3: Model hallucination leading to unsafe action
Mitigation:
- typed tools only
- policy gate before execution
- human confirmation for side effects
- explicit refusal on ambiguity for dangerous actions

### Threat 4: Filesystem abuse
Mitigation:
- sandboxed directories
- path normalization
- deny parent traversal
- deny sensitive directories by default

### Threat 5: Shell abuse
Mitigation:
- no arbitrary shell in MVP
- later shell access only through parameterized preapproved commands
- command templates instead of free-form strings

### Threat 6: Secret leakage
Mitigation:
- never print secrets into chat replies
- redact env variables and token-like strings from logs/results
- separate secret-loading layer from general runtime

---

## Model policy

The local model is an advisor, not the final authority.

### Current intended backend
Initial target: **DeepSeek** running locally.

Possible deployment options:
- Ollama
- vLLM
- llama.cpp wrappers
- LM Studio
- other local inference servers

### Model abstraction requirements
Implement a backend interface such as:

```python
class ModelBackend(Protocol):
    async def generate(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        ...
```

Requirements:
- backend must be swappable
- prompt templates must be versioned
- tool schemas must not be duplicated across modules
- model outputs must be parsed into structured decisions

### Model usage rules
- The model can propose a plan.
- The policy engine decides whether that plan is allowed.
- The executor runs only validated tool calls.
- Never execute natural-language shell snippets directly.

---

## Tool design standard

Every tool must be implemented as a typed capability.

### Tool contract
Each tool must expose:

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    risk_level: Literal["low", "medium", "high"]
    side_effects: bool
    requires_confirmation: bool
    timeout_sec: int
    input_model: type[BaseModel]
```

### Tool categories
Use categories such as:
- `info`
- `filesystem_read`
- `filesystem_write`
- `desktop_control`
- `automation`
- `network`
- `system`
- `shell_restricted`

### Approved design rules
- Prefer narrow tools over broad tools.
- Prefer parameterized operations over free-form commands.
- Prefer preapproved task runners over arbitrary script execution.
- Tool results must be JSON-serializable.
- Tool errors must be explicit and actionable.

### Example: good vs bad
Good:
- `read_text_file(path="notes/today.md")`
- `launch_app(app="firefox")` with allowlist
- `run_task(task_name="daily_report")`

Bad:
- `exec("rm -rf ~/Downloads")`
- `run_any_command(command="...")`
- `evaluate_python(code="...")` exposed to remote chat by default

---

## Policy engine rules

Implement a dedicated policy layer. Do not bury safety checks inside random tool functions.

### Policy responsibilities
- classify request intent
- detect sensitive or destructive actions
- determine whether confirmation is required
- block forbidden actions
- enforce per-tool and per-path limits
- produce a machine-readable decision

### Decision states
At minimum support:
- `allow`
- `allow_with_confirmation`
- `deny`
- `needs_clarification`

### Minimum confirmation triggers
Require confirmation for:
- deleting or overwriting files
- launching external network actions
- controlling keyboard/mouse
- running system commands
- modifying startup/system settings
- sending messages to third parties

### Confirmation UX
Use explicit yes/no confirmation tokens, for example:
- `CONFIRM <trace_id>`
- `CANCEL <trace_id>`

Avoid vague confirmation like “ok” where platform ambiguity could occur.

---

## Chat transport rules

The system will use a public chat platform as the remote interface.

Transport adapters must:
- normalize incoming messages into a shared internal format
- verify sender and chat context
- support text commands first
- avoid platform-specific business logic leakage into the core
- support rate limiting and deduplication

### Suggested normalized message shape

```python
@dataclass
class IncomingMessage:
    platform: str
    sender_id: str
    chat_id: str
    message_id: str
    text: str
    timestamp: datetime
    attachments: list[Attachment]
```

### Preferred initial command style
Support both:
- plain natural language for low-risk requests
- explicit slash-style commands for admin flows

Examples:
- `/status`
- `/jobs`
- `/approve TRACE123`
- `/safe_mode on`

---

## Coding standards

### General
- Use Python for the initial version unless the repository already establishes another runtime.
- Target Python 3.11+.
- Use type hints everywhere.
- Prefer small modules with explicit interfaces.
- Prefer composition over inheritance.
- Avoid global mutable state.

### Recommended libraries
Reasonable default choices:
- `pydantic` for schema validation
- `fastapi` only if a local admin API is needed
- `httpx` for HTTP clients
- `sqlalchemy` or `sqlite3` for local persistence
- `structlog` or standard `logging` for audit logs
- `pytest` for tests
- `tenacity` for bounded retries where appropriate

### Error handling
- Do not swallow exceptions silently.
- Convert operational failures into structured error objects.
- Distinguish user-facing errors from internal diagnostic details.

### Config
- Centralize config in one module.
- Load from environment plus optional local config file.
- Provide safe defaults.
- Include an `.env.example`, but never commit real secrets.

---

## Testing requirements

All agents must add tests for meaningful logic.

### Required test coverage areas
- sender verification
- policy decisions
- path allowlist enforcement
- tool schema validation
- confirmation flows
- audit logging generation
- model output parsing
- failure handling and timeout behavior

### Test strategy
- Unit tests for policy and tools
- Integration tests for transport -> policy -> executor flow
- Mock model responses in most tests
- Keep real local-model tests optional and isolated

---

## Logging and observability

Use structured logging.

### Every important event should log:
- trace_id
- module
- action
- result
- risk_level
- duration_ms

### Never log in plaintext if it includes:
- tokens
- passwords
- cookies
- full private file contents unless explicitly configured for debug in local-only mode

---

## Repository hygiene

Agents must keep the repository maintainable.

### Required files
- `README.md`
- `AGENTS.md`
- `.env.example`
- `.gitignore`
- `docs/architecture.md`
- `docs/security.md`
- `docs/threat-model.md`

### Recommended extras
- `Makefile`
- `scripts/dev.sh`
- `scripts/run_local.sh`
- `scripts/test.sh`

### Commit discipline
- Make small focused commits.
- Do not mix refactor and feature work unnecessarily.
- Update docs when architecture changes.

---

## Implementation priorities for Codex

When Codex helps implement this project, prioritize work in this order:

1. project skeleton and config loading
2. transport adapter abstraction
3. sender authentication / allowlist enforcement
4. typed tool interface
5. policy engine
6. audit logger
7. local DeepSeek backend abstraction
8. minimal safe tools
9. confirmation workflow
10. tests
11. scheduler and automation
12. optional UI/admin interface

---

## Explicit prohibitions

Agents must not introduce the following without explicit user approval:

- unrestricted remote shell execution
- self-updating code from remote instructions
- arbitrary code execution from model output
- automatic downloading and running external binaries
- credential harvesting or browser cookie extraction
- stealth persistence or hidden startup entries
- disabling OS security controls
- evasion, anti-forensics, or concealment features

---

## Example MVP backlog

A good first implementation backlog is:

- bootstrap Python project structure
- add config loader
- add `IncomingMessage` and transport interface
- add Telegram or other chosen adapter
- add admin allowlist verification
- add audit log event schema
- add `ToolSpec` and tool registry
- add 3 to 5 safe read-only tools
- add policy engine with confirmation states
- add DeepSeek backend wrapper
- add end-to-end message handling loop
- add tests for core flows

---

## Definition of done

A feature is only done when:
- code is implemented
- config impact is documented
- security impact is reviewed
- logs are emitted correctly
- tests are added or updated
- user-facing behavior is clear on success and failure

---

## Guidance for future extensions

When extending this project later, prefer:
- replacing single-model logic with backend abstraction
- adding per-tool permission profiles
- adding user-local web dashboard for approvals and logs
- adding job queue and recurring summaries
- adding RAG over local files only after access controls are stable
- adding browser/desktop automation only after strong policy gating exists

---

## Final instruction to all coding agents

When uncertain, choose the option that is:
1. narrower in scope
2. easier to audit
3. easier to disable
4. safer by default
5. more modular for later replacement

Build a system that is useful on day one, but still trustworthy when it gains more power later.
