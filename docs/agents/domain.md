# Domain docs

## Layout

This repository uses a single-context layout:

- `GLOSSARY.md` at the repository root defines domain vocabulary.
- `docs/decisions/` holds architecture decision records (ADRs).
- `docs/domain/` describes current domain behavior.

Keep the existing ADR location; do not create a parallel `docs/adr/` tree.

## Before exploring

Read the root glossary when present and the ADRs relevant to the work.
Consult relevant domain documentation and verify behavior against code
and tests.

If the glossary does not exist, proceed silently. Do not create placeholder
domain documents. Domain-modeling work can create it when terms are resolved.

## Vocabulary and decisions

Use glossary terms consistently in issue titles, proposals, and tests.
When a necessary concept is missing, assess whether it represents a real
vocabulary gap for domain-modeling work.

Explicitly identify proposals that conflict with an existing ADR and explain
why the decision should be reconsidered.
