# Execution Engine — Wave Execution, Plan-Check, Fresh Context

This document defines how dev-team executes tasks on Digitus Engine V2 (FastAPI/Celery backend, React/Vite frontend).

## 1. Wave Execution — Dependency-Based Parallel Execution

Tasks are grouped into "waves" based on the dependency graph. Tasks within the same wave run in parallel; the next wave only starts once the previous one is complete.

### How It Works

```
PM performs task breakdown:
  TASK-001: Auth service (Backend)        → no dependency
  TASK-002: User model (Backend)          → no dependency
  TASK-003: Login UI (Frontend)           → depends on TASK-001
  TASK-004: Profile UI (Frontend)         → depends on TASK-002
  TASK-005: Auth integration test (QA)    → depends on TASK-001 + TASK-003

Wave planning:
  Wave 1: [TASK-001, TASK-002]  → Parallel (independent)
  Wave 2: [TASK-003, TASK-004]  → Parallel (depends on Wave 1)
  Wave 3: [TASK-005]            → Depends on Wave 2
```

### Wave Planning Rules

1. Tasks with no dependencies → Wave 1
2. Tasks that depend only on Wave 1 → Wave 2
3. And so on...
4. Tasks modifying the same file cannot be in the same wave (conflict risk)
5. Maximum 3-4 parallel agents per wave (for context management)

### Wave Output Format

```
🌊 WAVE EXECUTION PLAN
━━━━━━━━━━━━━━━━━━━━━

Wave 1 (Parallel):
  ├── TASK-001 → 🔧 Backend Dev (Balanced, low) — Auth service
  └── TASK-002 → 🔧 Backend Dev (Balanced, low) — User model
  ⏱️ Estimated: ~2 minutes

Wave 2 (Parallel, after Wave 1):
  ├── TASK-003 → 💻 Frontend Dev (Balanced, low) — Login UI
  └── TASK-004 → 💻 Frontend Dev (Balanced, low) — Profile UI
  ⏱️ Estimated: ~3 minutes

Wave 3 (after Wave 2):
  └── TASK-005 → 🧪 QA (Balanced, low) — Integration test
  ⏱️ Estimated: ~1 minute

━━━━━━━━━━━━━━━━━━━━━
📊 Total: 3 waves, 5 tasks, estimated ~6 minutes
```

## 2. Plan-Check Loop — Automated Verification

While applying any plan, a "do → verify → fix" loop runs. This ensures errors are caught early.

### Loop Flow

```
Agent performs the task
    │
    ▼
QA/Checker verifies automatically:
    ├── Does the code compile? (dart analyze)
    ├── Do existing tests pass?
    ├── Are acceptance criteria met?
    ├── Does it conform to architectural standards?
    └── Any security vulnerabilities?
    │
    ├── ✅ Passed → Continue to next wave
    │
    └── ❌ Failed → Send feedback to the agent
         │
         ├── Attempt 1: Agent fixes it, re-check
         ├── Attempt 2: Agent fixes it, re-check
         └── Attempt 3: Failed → Escalate
              │
              ├── Simple issue → Consult the Architect
              └── Major issue → Notify the user
```

### Automated Check List

Checks run whenever a task is completed:

**Backend (Python/FastAPI):**
```bash
# The ONLY correct way — an isolated test DB. Never the app container,
# never a production DATABASE_URL: the fixtures truncate tables.
docker-compose -f docker-compose.test.yml run --rm test_app pytest tests/ -v

# Migration chain from an empty database
docker-compose -f docker-compose.test.yml run --rm test_app alembic upgrade head
```

**Frontend (React/TS) — all four, all required:**
```bash
cd frontend && npm run lint && npm run format:check && npm run build && npm run test
```

A first backend run showing many ERRORs can be a known flake; run it again. A
clean second run means no regression — report **both** results, not the good one.

If a check was not run, the report says "not run" and why. Silence reads as
success, and a check that tested nothing is worse than no check.

If these checks fail, the agent receives feedback along with the error message.

### Plan-Check Output Format

```
✅ PLAN-CHECK RESULT
━━━━━━━━━━━━━━━━━━━
Task: TASK-003 (workspace keyword refresh endpoint)
Agent: 🔧 Backend Dev

Checks:
  ✅ pytest (test_app) — 2406 passed, 0 failed
  ✅ alembic upgrade head — clean from empty DB
  ⚠️ Acceptance Criteria — 3/4 met
     ❌ workspace isolation test for the new endpoint missing

Status: RETRY (Attempt 1/3)
Feedback: add the cross-workspace 404 test (acceptance criteria #4)
━━━━━━━━━━━━━━━━━━━
```

## 3. Fresh Context — Clean Context Management

Every agent starts a new task with a clean context window. This prevents "context rot".

### Why It Matters

In a long-running session, the context window starts filling up. Agents get confused by leftover information from previous tasks, hallucination risk increases, and code quality drops.

### How It Works

```
Orchestrator assigns a task:
    │
    ▼
Spawn a new subagent (clean context)
    │
    ├── Load only the necessary files into it:
    │   ├── STATE.md (current status)
    │   ├── Relevant ARCHITECTURE.md section
    │   ├── Task definition and acceptance criteria
    │   └── Relevant source files (only ones to be changed)
    │
    ├── Agent completes the task
    │
    ├── Returns output to the orchestrator
    │
    └── Subagent context is discarded
```

### Context Budget

Limit the context sent to each agent:

| Task Size | Max Context | Files to Send |
|-------------|-------------|----------------------|
| Small (S) | ~10K tokens | STATE.md + 1-2 source files |
| Medium (M) | ~30K tokens | STATE.md + ARCHITECTURE summary + 3-5 source files |
| Large (L) | ~80K tokens | STATE + ARCH + REQUIREMENTS + all relevant files |
| Extra Large (XL) | ~200K tokens | All of .dev-team/ + broad source file set |

### Orchestrator Context Management

The orchestrator (main session) also keeps its own context clean:
- The orchestrator only coordinates, it doesn't do heavy work itself
- It keeps only a summary of agent outputs, not the full output
- STATE.md is always the source of truth — not the orchestrator's memory

## 4. Atomic Commits

Every task creates its own commit. This means:
- If something breaks, only that task can be reverted
- Debugging is easier (git bisect)
- Code review is cleaner

### Commit Flow

```
Agent completes the task
    │
    ▼
Did Plan-Check pass?
    │
    ├── ✅ Yes → Create atomic commit
    │   │
    │   └── git commit -m "feat(scoring): add workspace keyword refresh
    │
    │                       Task: TASK-001
    │                       Agent: Backend Dev (Balanced, low)
    │                       Wave: 1"
    │
    └── ❌ No → No commit, enter the fix loop
```

### Commit Message Format

```
<type>(<scope>): <short description>

Task: TASK-XXX
Agent: [agent name] ([model])
Wave: [wave number]

[detailed description if any]
```

Types: feat, fix, refactor, test, docs, style, chore

## 5. Architect Step — Conditional, and the Council Runs With It

The Architect step in the pipeline is **conditional**, not mandatory. It runs only when the task involves a genuinely new architectural decision (new state-management approach, new data model, new package with broad impact). It is **skipped** when the task is a known pattern already covered by the existing `ARCHITECTURE.md` (e.g. "add another CRUD screen using the existing pattern", "add a field to an existing model").

When the Architect step does run, it should be model-efficient:
- The Architect (Frontier, effort: medium, conditional — user ruling 2026-09-08) emits a **short decision output only**: the chosen approach, a one-paragraph rationale, and any constraints for implementers. It does not write implementation code.
- The actual code is then written by the Balanced-class dev agents (effort: low), using the Architect's short decision as part of their context (see context-engineering.md's prompt ordering: the Architect's decision becomes part of the semi-stable ARCHITECTURE.md layer for subsequent tasks).

This keeps the expensive, high-effort reasoning step short and cheap to run, while the higher-volume code generation work goes to the cheaper Balanced-class agents.

**The council is gated on the same condition.** When the Architect Gate opens, the
same question also goes to the cross-vendor council — **blind**, without showing it
the Architect's answer — and the two are compared before anything reaches the user.
Gate closed means the council does not run either. Every dev-team agent is Claude,
so agreement among them is shared priors, not corroboration; the council is the only
place independent evidence enters. Rules, modes and the framing discipline that
decides whether the answer is worth anything: `references/council-integration.md`.

Reply to the user in the language the user used.
