# Threat Model

Primary threats considered in the current MVP:

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

The model is advisory only. The executor never converts free-form natural language directly into shell execution.
