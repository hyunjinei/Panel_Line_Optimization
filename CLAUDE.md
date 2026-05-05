@AGENTS.md
@docs/project_map.md
@docs/constraint_catalog.md

## Claude Code

### Purpose
- This file is the Claude entrypoint for this repository.
- Keep the root file short, and move detail into imported docs instead of expanding this file indefinitely.
- Use the imported docs as the default context for every session.

### Read Order
- Read imported files in this order when reasoning about project behavior:
  1. `docs/constraint_catalog.md`
  2. `docs/project_map.md`
  3. `AGENTS.md`
- If old audit/ppt/paper docs conflict with the imported files, trust the imported files.

### Session Rules
- Use Korean for user-facing explanations.
- Do not guess about constraint behavior. Verify with code, raw CSV rows, or a reproducible run.
- Prefer small forced-case reproductions before trusting real-data outcomes for constraint claims.
- Preserve current behavior unless the user explicitly asks for a change.
- Treat Excel files under `environment/` as external data sources. Do not change their schema.

### Verification Rules
- After Python edits, run at least one focused verification step.
  - `python3 -m py_compile <file>`
  - a focused reproduction or smoke test
- After YAML edits, verify with `yaml.safe_load`.
- When comparing constraints, explicitly separate:
  - `primary/raw`
  - `info`
  - `audit-off` items that are not part of the current absolute final metric

### Current Project Decisions
- `ROUTING_WORKSHOP_ORDER`
  - runtime: off
  - final audit: on
- `CONSECUTIVE_3BAY`
  - default runtime: off
  - default final audit counting: off
  - interpret real bay streak violations through `P7#7`
- LLM layers remain side-car interfaces.
  - They may parse requests, explain outcomes, and recommend what-if alternatives.
  - They must not replace scheduler execution or final constraint counting.

### Context Hygiene
- If a rule is rarely needed, move it into a path-specific doc or experiment note.
- If a behavior must happen every time with no exceptions, enforce it in code or hooks rather than relying on prose alone.
