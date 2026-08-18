# Architecture decision records

Use this directory for future durable, cross-cutting decisions. Current documentation describes verified behavior and does not invent historical rationale.

## When to add an ADR

Add an ADR when a change affects deployment boundaries, Docker/runtime orchestration, authentication ownership, database technology, worker topology, file-storage ownership, process isolation, or another decision that future contributors must understand.

Routine bug fixes, type cleanup, and local UI changes do not need an ADR.

## Template

```markdown
# ADR-NNNN: Decision title

- Status: proposed | accepted | superseded
- Date: YYYY-MM-DD
- Owners: role or team, without personal data

## Context

Describe the verified problem, constraints, and forces.

## Decision

State the chosen approach precisely.

## Alternatives considered

Record realistic alternatives and why they were not selected.

## Consequences

List positive and negative operational, development, data, and migration effects.

## Verification and follow-up

State how the decision will be validated and any follow-up work.
```

Never put credentials, real host paths, database records, user uploads, or process identifiers in an ADR.
