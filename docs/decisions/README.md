# Architecture decision records

This directory contains accepted cross-cutting decisions. Code and tests establish current behavior. A decision record documents verified choices and their operational tradeoffs; it does not replace source.

## Accepted decisions

| ADR | Decision | Status |
| --- | --- | --- |
| [ADR-0001](0001-standard-library-operation-logging.md) | Use standard-library logging for application operations | Accepted |
| [ADR-0002](0002-atomic-local-configuration.md) | Atomic local configuration snapshots and updates | Accepted |
| [ADR-0003](0003-lifecycle-and-download-ownership.md) | Durable instance lifecycle and download ownership | Accepted |

ADR-0003 records the accepted lifecycle, managed-file, and download-ownership decisions and their operational limits.

## When to add an ADR

Add an ADR when a change affects deployment boundaries, runtime orchestration, authentication ownership, database technology, worker topology, file-storage ownership, process isolation, or another durable choice future contributors need to understand.

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
