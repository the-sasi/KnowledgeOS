# services/guardrails

Validation and policy enforcement on the way in and on the way out.

Scope (not implemented):

- input validation and prompt-injection defense
- output schema and citation checks
- PII and sensitive-content handling
- refusal and escalation policy

Guardrails are a separate module so they can be applied consistently across
`apps/` and `services/agents/`.
