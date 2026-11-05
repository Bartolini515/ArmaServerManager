# Issue tracker: Local Markdown

Issues and specs live as Markdown files under `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`.
- Specification: `.scratch/<feature-slug>/spec.md`.
- One file per implementation ticket:
  `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`.
- Record triage state in a `Status:` line near the top.
  Use the roles in `triage-labels.md`.
- Append conversation history under `## Comments`.
- Use anonymized examples; never include sensitive operational data.

## Publish and fetch

When a skill says "publish to the issue tracker", create the corresponding
local file. When it says "fetch the relevant ticket", read the referenced
file. Resolve bare issue numbers within the relevant feature directory;
ask when ambiguous.

## Wayfinding operations

- Map: `.scratch/<effort>/map.md`, containing Notes, Decisions-so-far,
  and Fog sections.
- Child ticket: `.scratch/<effort>/issues/<NN>-<slug>.md`.
- Record ticket type in `Type:`:
  `research`, `prototype`, `grilling`, or `task`.
- Record workflow state separately in `Workflow: open`,
  `Workflow: claimed`, or `Workflow: resolved`; preserve the triage
  `Status:` field.
- Record dependencies in `Blocked by: NN, NN`.
  A ticket is unblocked when all listed tickets are resolved.
- Frontier: select the first open, unblocked, unclaimed ticket by number.
- Claim: save `Workflow: claimed` before starting work.
- Resolve: append the result under `## Answer`, set `Workflow: resolved`,
  and add a summary and relative link to the map's Decisions-so-far.
