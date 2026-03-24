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

Not implemented yet:

- transport-specific sender signatures
- encrypted secrets store
- desktop automation
- browser automation
- unrestricted shell access
