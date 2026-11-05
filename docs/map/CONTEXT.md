# System map — reading and maintenance contract

## Inputs

- Working: the task and relevant source and tests linked by the selected card.
- Reference: [root instructions](../../AGENTS.md), the nearest backend or frontend instructions, and the selected card's documentation links.
- Do not load every card: start with [change impact](effects/CONTEXT.md) or the appropriate catalog.

## Process

1. Select the relevant object or process; follow its source links and verify claims against current code and tests.
2. Update affected cards alongside implementation and existing documentation duties. Cards summarize navigation, not the complete contract.
3. Mark cards stale when their claims cannot be verified. `live` means implemented, `leftover` means a secondary existing path, and `ghost` means named but not wired.
4. When an exposed capability or its main implementation changes, update the [feature and function map](features.md) and verify its links.

## Outputs

- Updated cards and links when a change affects them; cite source and record the verification date and revision or working-tree state.

## Check

Run `pipenv run check` from the repository root and verify relevant card links and claims. Django tests are excluded by that check. A cold reader should reach a relevant card from the root in two links and load only task-relevant context.
