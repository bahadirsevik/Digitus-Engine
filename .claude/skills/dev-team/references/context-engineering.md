# Context Engineering — Project Memory System

Dev-team maintains a structured memory system for every project. These files live in the project's `.dev-team/` folder and help agents make correct decisions.

## Digitus Has Prior Memory — Read It First

This project is not a blank slate. Three sources already exist and **outrank** anything
in `.dev-team/`:

| Source | What it holds | Rule |
|--------|---------------|------|
| `CLAUDE.md` (repo root) | The architecture of record: stack, layer contracts, deliberate exceptions, known traps | **`ARCHITECTURE.md` never contradicts it.** It cites and extends it |
| The memory index (`MEMORY.md` + `memory/*.md`) | Measured findings, closed decisions, boss rulings, cost rules | Read before proposing an experiment — most have been run |
| `plan_*.md` (repo root) | Canonical plan documents per workstream | The plan of record for that workstream |

A `.dev-team/` file that restates one of these is duplication, and duplication goes
stale. Cite instead: *"see CLAUDE.md §4.11"*, not a copied paragraph. `.dev-team/`
earns its place only where those three are silent — current task state, and decision
records for decisions taken from here on.

## File Structure

The following structure is created when the first task starts on any project:

```
<project>/
├── .dev-team/
│   ├── PROJECT.md          ← Project vision, goals, constraints
│   ├── REQUIREMENTS.md     ← Comprehensive requirements (v1/v2)
│   ├── ROADMAP.md          ← Phases, progress status
│   ├── STATE.md            ← Current status, blockers, active decisions
│   ├── ARCHITECTURE.md     ← Architectural decisions, patterns, data model
│   ├── CONTEXT-MAP.md      ← Context map across agents
│   ├── decisions/
│   │   ├── ADR-001-*.md    ← Architecture Decision Records
│   │   └── ...
│   ├── research/
│   │   ├── *.md            ← Research notes
│   │   └── ...
│   └── sprints/
│       ├── sprint-1.md
│       └── ...
```

## File Definitions

### PROJECT.md — Project Identity
Fixed once created, rarely changes. Written by the CEO and Architect.

```markdown
# [Project Name]

## Vision
[One-sentence project vision]

## Target User
[Who this is being built for]

## Core Value Proposition
[Why this app exists]

## Tech Stack
- Backend: FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL 15
- Async: Celery + Redis
- Frontend: React 18 + TypeScript + Vite + Zustand
- AI: Gemini (google-genai), DeepSeek
(Authoritative version table: `CLAUDE.md` §2 — do not copy it here.)

## Constraints
- [Platform constraints]
- [Budget constraints]
- [Time constraints]

## Success Criteria
- [Metric 1]
- [Metric 2]
```

### REQUIREMENTS.md — Requirements
Created and maintained by the PM. Two levels: v1 (MVP) and v2 (future).

```markdown
# Requirements

## v1 — MVP
### Functional
- [FR-001] User must be able to register/log in
- [FR-002] ...

### Non-Functional
- [NFR-001] App must launch within 3 seconds
- [NFR-002] ...

## v2 — Next Release
- [FR-101] ...

## Out of Scope
- [things explicitly not being built]
```

### ROADMAP.md — Roadmap
Managed by the PM, approved by the CEO.

```markdown
# Roadmap

## Phase 1: [Phase Name] — [Status: ✅ / 🔄 / ⏳]
- [x] Task 1
- [ ] Task 2
- Start: [date]
- Estimated Finish: [date]

## Phase 2: [Phase Name] — ⏳
- [ ] Task 3
- [ ] Task 4
```

### STATE.md — Current Status (Most Critical File)
Updated after every task. Agents read this first before starting work.

```markdown
# Current Status

Last Updated: [date-time]
Active Phase: [phase number and name]
Active Sprint: [sprint number]

## Currently In Progress
- [TASK-XXX]: [description] → [agent] — [% progress]

## Blockers
- [any blocking issues]

## Recent Decisions
- [summary of last 5 decisions with ADR reference]

## Pending Approvals
- [decisions awaiting the user]

## Technical Debt
- [known but deferred issues]

## Next Steps
- [next 3-5 tasks]
```

### ARCHITECTURE.md — Architecture Map
Created and maintained by the Architect.

```markdown
# Architecture

## Overall Structure
[Architecture diagram / description]

## Folder Structure
[lib/ structure]

## State Management
[Chosen approach and rationale]

## Data Model
[Collection/table structure]

## Security Rules
[Auth flow, security rules summary]

## Package List
| Package | Version | Purpose |
|-------|---------|------|
| ... | ... | ... |
```

### CONTEXT-MAP.md — Context Map
Which agent knows what, and has access to what. The orchestrator reads this file to decide which files to send to which agent.

```markdown
# Context Map

## Agent → File Access Matrix
| Agent | Must Read | Must Write |
|-------|----------------|-----------------|
| CEO | PROJECT, ROADMAP, STATE | PROJECT, ROADMAP |
| Architect | PROJECT, ARCHITECTURE, STATE | ARCHITECTURE |
| PM | REQUIREMENTS, ROADMAP, STATE | REQUIREMENTS, ROADMAP, STATE |
| Frontend Dev | CLAUDE.md, ARCHITECTURE, STATE, relevant feature/ | feature files |
| Backend Dev | CLAUDE.md, ARCHITECTURE, STATE, data model | backend files |
| QA | CLAUDE.md, STATE, ARCHITECTURE, tests/ | test files |
| UI/UX | PROJECT, ARCHITECTURE, STATE | design specifications |
| Memo | All (read) | decisions/, sprints/, STATE |

## Feature → Agent Matrix
| Feature | Responsible Agents |
|---------|-------------------|
| Scoring / channel assignment | Backend (+ Architect if the gate opens) |
| Workspace / brand profile | Backend + Frontend |
| Keyword import + dedup | Backend |
| Generation (SEO/ADS/Social) | Backend + QA (grounding rules) |
| Dashboard / export UI | UI/UX + Frontend |
```

## File Lifecycle

1. **Project Start**: CEO + Architect → create `PROJECT.md` and `ARCHITECTURE.md`
2. **Planning**: PM → creates `REQUIREMENTS.md` and `ROADMAP.md`
3. **Before Every Task**: Agent → reads `STATE.md` (current context)
4. **After Every Task**: Memo → updates `STATE.md`, and other files if needed
5. **After Every Decision**: Memo → writes `decisions/ADR-XXX.md`
6. **End of Sprint**: PM + Memo → write `sprints/sprint-N.md`, update `ROADMAP.md`

## Context Scoping — Who Sees What

Instead of sending the entire project context to every agent, send only the files each agent needs. This both saves cost and prevents "context rot".

For small tasks (bug fix, small UI change):
- `STATE.md` + the relevant source files

For medium tasks (new component, new API endpoint):
- `STATE.md` + the relevant `CLAUDE.md` section + the feature's files

For large tasks (new feature, architectural change):
- `PROJECT.md` + `REQUIREMENTS.md` + `CLAUDE.md` + `ARCHITECTURE.md` + `STATE.md`
- Plus any `plan_*.md` and memory entry that already covers this ground

For strategic decisions:
- All `.dev-team/` files

## Prompt Ordering for Caching

Order the content sent to an agent so that the most stable material comes first and the most variable material comes last:

1. **Stable** (first): agent definition/system prompt, reference docs (e.g. this file, communication-protocol.md, execution-engine.md, model-selection-guide.md) — these almost never change between calls.
2. **Semi-stable** (second): `ARCHITECTURE.md`, `REQUIREMENTS.md`, `PROJECT.md` — these change occasionally, at phase or decision boundaries.
3. **Variable** (last): `STATE.md` and the actual task/instructions — these change on every single call.

Why this matters: prompt caching reuses the previously-processed prefix of a prompt across calls, and a cache hit costs roughly 90% less than a fresh read of the same content. If a prompt is built the wrong way around — variable content (like the task or STATE.md) placed at the top — every call has a different prefix, so nothing hits the cache and the full cost is paid every time. Keeping the stable reference material first, and always in the same exact text, lets consecutive agent calls reuse that cached prefix and only pay full price for the small variable tail (STATE.md + task).

Practical rule: when assembling any agent's context, concatenate in this fixed order: agent definition → static reference docs → ARCHITECTURE.md/REQUIREMENTS.md/PROJECT.md → STATE.md → task description. Never reorder or reformat the stable sections between calls, or the cache will be invalidated.

Reply to the user in the language the user used.
