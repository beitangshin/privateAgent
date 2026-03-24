# Architecture

## Current Direction

`privateAgent` will use a split architecture:

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
            -> Optional automation tools
  -> Local Audit Logger
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

## Recommended Request Flow

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

This is the main future path for cloud-assisted control.

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

The existing module layout still fits this architecture:

```text
transport  -> Telegram message normalization
auth       -> sender and chat verification
policy     -> risk, allow/deny, confirmation rules
agent      -> orchestration and planning flow
models     -> DeepSeek backend abstraction
executor   -> typed tool execution runtime
tools      -> local capabilities only
audit      -> structured logs
storage    -> state, approvals, job metadata
config     -> environment-driven settings
```

## Suggested Model Layer Extension

The `models` package should evolve into two explicit backends:

- `mock.py`: local tests and development
- `deepseek_cloud.py`: cloud planner backend

Recommended interface:

```python
class ModelBackend(Protocol):
    async def plan(self, messages: list[dict], tools: list[dict]) -> ModelPlan:
        ...

    async def summarize(self, messages: list[dict], context: dict) -> str:
        ...
```

Why split `plan` and `summarize`:

- planning needs strict structured output
- summarization can stay freer and more natural
- it is easier to audit which model call caused which tool execution

## Security Boundary

This architecture only stays safe if the boundary is hard.

Mandatory rules:

- the cloud model never receives raw secrets by default
- the cloud model never receives full file contents unless explicitly allowed
- the local agent trims and redacts tool output before sending it upward
- dangerous tools require local confirmation even if the model insists otherwise
- local policy can deny any plan regardless of model confidence
- local safe mode can disable entire tool categories

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
- for health checks, send structured metrics instead of verbose dumps

## Observability

To make cloud-assisted control debuggable, log both sides of the flow.

### Local audit log should record

- trace ID
- Telegram sender and chat
- original user message
- whether the request was command-mode or model-mode
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
- structured plan returned
- latency
- parse failures

This should live separately from the general tool audit trail.

## Recommended Runtime Modes

Support these modes explicitly:

- `command_only`: only slash commands, no cloud model
- `cloud_plan_local_execute`: cloud DeepSeek plans, local bot executes
- `confirmation_only`: any side-effecting tool requires approval
- `safe_mode`: deny risky categories entirely

For your use case, the best default is:

- `cloud_plan_local_execute`
- plus `safe_mode=true` at first

## Practical Rollout Plan

### Phase A

Keep the current command bot working and add richer monitoring tools.

Good next tools:

- `get_system_health`
- `get_disk_usage`
- `get_top_processes`
- `get_network_summary`

### Phase B

Add the cloud DeepSeek planner backend without any write-capable tools.

Allowed first use cases:

- summarize system health
- explain logs
- choose which safe read-only tools to call

### Phase C

Add confirmation-aware write tools.

Examples:

- `write_note`
- `archive_file`
- `run_preapproved_task`

### Phase D

Only then consider automation tools such as browser or desktop control.

## Recommendation

For this project, the strongest architecture is:

- Telegram stays the remote UI
- your PC stays the execution host
- DeepSeek in the cloud acts as planner and explainer
- local policy remains the real gatekeeper
- all execution stays inside typed audited local tools

That gives you the convenience of a stronger remote brain without giving up control of the machine.
