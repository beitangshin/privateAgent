# Architecture

## Current Direction

`privateAgent` uses a split architecture:

- a cloud-hosted DeepSeek model as the control brain
- a local Telegram bot as the trusted execution agent
- a strict policy and tool boundary between the two

The key rule is simple:

- the cloud model can suggest
- the local agent decides
- only local typed tools can execute

This keeps the system useful remotely without letting raw model text become machine control.

## Target Shape

```text
Telegram
  -> Local Transport Adapter
  -> Local Auth / Sender Allowlist
  -> Local Command Router
  -> Local Policy Engine
  -> Local Agent Orchestrator
       -> Cloud DeepSeek Planner
       -> Local Execution Plan Validator
       -> Local Tool Runtime
            -> Read-only monitoring tools
            -> Controlled write tools
            -> Repo-safe development tools
            -> Optional automation tools
  -> Local Audit Logger
  -> Local Model Call Logger
  -> Local State Store
  -> Telegram Reply
```

## Responsibility Split

### Cloud DeepSeek control plane

The cloud model is responsible for:

- understanding natural-language requests
- turning user intent into structured plans
- choosing candidate tools and arguments
- generating user-facing summaries
- asking for clarification when the request is ambiguous

The cloud model is not allowed to:

- execute shell commands directly
- access the local filesystem directly
- bypass local policy checks
- see more local data than the local agent explicitly sends

### Local bot execution plane

The local bot is responsible for:

- verifying Telegram sender identity
- applying local safety policy
- deciding whether a tool call is allowed
- requesting confirmation when needed
- executing typed local tools
- logging requests, plans, tool calls, and results
- redacting secrets and limiting data sent back to the cloud

This means the local machine remains the final authority.

## Request Flows

### Low-risk command flow

Use this for slash commands like `/ping`, `/status`, `/sysinfo`, `/list`, `/read`.

```text
Telegram message
  -> Local parser
  -> Local policy
  -> Local tool runtime
  -> Audit log
  -> Telegram response
```

No cloud model is needed here. This keeps basic monitoring fast, cheap, and private.

### Natural-language agent flow

Use this for requests like:

- "check whether disk space is running low"
- "read today's log and summarize the error"
- "tell me if the machine looks healthy"

```text
Telegram message
  -> Local auth
  -> Local context builder
  -> Cloud DeepSeek planning call
  -> Structured tool plan
  -> Local policy validation
  -> Local tool execution
  -> Optional second cloud summarization call
  -> Audit log
  -> Telegram response
```

### Repo development flow

Use this for controlled remote programming tasks.

Examples:

- "show me the diff in FridgeSystem"
- "search for FoodItemDao usage"
- "run tests in privateAgent"
- "open the README for FridgeSystem"

```text
Telegram message
  -> Local auth
  -> Repo session selector
  -> DeepSeek structured repo plan
  -> Local repo policy validation
  -> Repo-safe tool runtime
       -> read_repo_file
       -> search_repo
       -> list_repo_dir
       -> run_repo_command
       -> show_repo_diff
       -> git_commit (confirmation)
       -> git_push (confirmation)
  -> Audit log
  -> Telegram response
```

This is the recommended path for "remote coding" without exposing arbitrary shell access.

## Planning Contract

Do not let the cloud model return free-form actions only. It should return a structured plan.

Recommended plan schema:

```json
{
  "intent": "inspect_system_health",
  "requires_confirmation": false,
  "steps": [
    {
      "tool_name": "capture_system_info",
      "arguments": {}
    },
    {
      "tool_name": "get_system_health",
      "arguments": {}
    }
  ],
  "response_style": "short_status"
}
```

Local code should reject:

- unknown tools
- malformed arguments
- arguments outside allowlisted ranges
- plans that exceed step or size limits

## Core Local Modules

The current module layout supports this architecture:

```text
transport  -> Telegram message normalization
auth       -> sender and chat verification
policy     -> risk, allow/deny, confirmation rules
agent      -> orchestration and planning flow
models     -> DeepSeek backend abstraction
executor   -> typed tool execution runtime
tools      -> monitoring and safe local capabilities
audit      -> structured logs and model call logs
storage    -> state, approvals, job metadata
config     -> environment-driven settings
```

Future remote development support should add:

```text
repo       -> repository registry, repo session state, repo command allowlists
```

## Repo-Safe Development Mode

This project should not expose raw shell execution from Telegram.

Instead, remote development should be modeled as a restricted capability layer.

### Required constraints

- operations are limited to allowlisted repositories
- each Telegram session is bound to one active repository
- commands are selected from an allowlisted command registry
- file edits are restricted to repo roots
- dangerous git commands require confirmation or are denied
- system directories remain inaccessible

### Recommended repo tool set

- `list_repo_dir`
- `read_repo_file`
- `search_repo`
- `write_repo_patch`
- `run_repo_command`
- `show_repo_diff`
- `git_commit_repo`
- `git_push_repo`

### Example repo command registry

Use named commands, not free-form shell text.

```json
{
  "pytest": {
    "argv": ["python", "-m", "pytest"],
    "allowed_extra_args": ["-q", "-k", "tests/test_agent_flow.py"],
    "timeout_sec": 120
  },
  "gradle_test": {
    "argv": ["./gradlew", "test"],
    "allowed_extra_args": [],
    "timeout_sec": 300
  },
  "git_status": {
    "argv": ["git", "status", "--short", "--branch"],
    "allowed_extra_args": [],
    "timeout_sec": 30
  }
}
```

The key is that the model chooses a command ID, not an arbitrary command string.

## Security Boundary

This architecture only stays safe if the boundary is hard.

Mandatory rules:

- the cloud model never receives raw secrets by default
- the cloud model never receives full file contents unless explicitly allowed
- the local agent trims and redacts tool output before sending it upward
- dangerous tools require local confirmation even if the model insists otherwise
- local policy can deny any plan regardless of model confidence
- local safe mode can disable entire tool categories
- repo development mode must never imply unrestricted system shell access

## Data Minimization Strategy

When using a cloud model, send the least data needed.

Preferred order:

1. send metadata only
2. send short excerpts
3. send sanitized structured summaries
4. send raw content only for explicitly approved workflows

Examples:

- for log triage, send the last 50 relevant lines, not the whole file
- for file inspection, send path, size, and a bounded excerpt
- for repo debugging, send just the relevant file snippet, not the entire repository

## Observability

To make cloud-assisted control debuggable, log both sides of the flow.

### Local audit log should record

- trace ID
- Telegram sender and chat
- original user message
- whether the request was command-mode, model-mode, or repo-dev-mode
- model backend used
- model plan summary
- local policy decision
- tools executed
- tool arguments after validation
- redaction decisions
- final response summary
- errors and timeouts

### Model call log should record

- request timestamp
- model name
- prompt template version
- sanitized request payload
- raw model output
- reasoning content when available
- structured plan returned
- parse failures

### Repo execution log should record

- active repository
- chosen command ID
- approved extra arguments
- working directory
- exit code
- stdout/stderr summary
- whether confirmation was required

## Recommended Runtime Modes

Support these modes explicitly:

- `command_only`: only slash commands, no cloud model
- `cloud_plan_local_execute`: cloud DeepSeek plans, local bot executes
- `confirmation_only`: any side-effecting tool requires approval
- `safe_mode`: deny risky categories entirely
- `repo_dev_mode`: allow repo-safe development tools only inside allowlisted repositories

For your use case, the best default is:

- `cloud_plan_local_execute`
- plus `safe_mode=true`
- plus `repo_dev_mode` only when explicitly enabled

## Practical Rollout Plan

### Phase A

Keep the current command bot working and maintain rich monitoring tools.

### Phase B

Use DeepSeek for read-only natural-language planning and summary generation.

### Phase C

Add repo-safe development tools with strict repo and command allowlists.

Allowed first use cases:

- read files in an allowlisted repo
- search code
- run tests
- inspect git status and diff

### Phase D

Add confirmation-aware write tools for repository changes.

Examples:

- write a patch
- commit changes
- push changes

### Phase E

Only then consider broader automation.

## Recommendation

For this project, the strongest architecture is:

- Telegram stays the remote UI
- your PC stays the execution host
- DeepSeek in the cloud acts as planner and explainer
- local policy remains the real gatekeeper
- all execution stays inside typed audited local tools
- remote programming is implemented as repo-safe tooling, not arbitrary shell

That gives you the convenience of a stronger remote brain without giving up control of the machine.
