# Agent Communication Protocol

This document defines how agents communicate with each other. All agents must follow this protocol.

## Message Structure

Every inter-agent communication must follow this structure:

```json
{
  "from": "agent-role",
  "to": "agent-role | broadcast",
  "type": "task | decision | question | report | blocker | review-request",
  "priority": "critical | high | normal | low",
  "subject": "Short title",
  "body": "Detailed content",
  "context": {
    "related_tasks": ["TASK-001"],
    "related_files": ["lib/features/auth/..."],
    "dependencies": []
  },
  "requires_response": true,
  "deadline": "immediate | next-task | end-of-sprint"
}
```

## Message Types

### 1. Task (Task Assignment)
Used when the PM assigns tasks to dev agents.

```
PM → Frontend Dev:
[TASK] TASK-005: Build the relevance results table
Priority: High
Acceptance Criteria:
  - Sortable columns (keyword, score, relevance)
  - Loading / empty / error states
  - Workspace-scoped fetch (no request with undefined brand_profile_id)
  - Turkish copy consistent with the existing pages
Dependencies: TASK-003 (relevance endpoint) must be completed
Model: Balanced, effort: low
```

### 2. Decision (Decision Announcement)
Used to announce decisions made by the CEO or Architect to relevant agents.

```
Architect → Broadcast:
[DECISION] Generated artefacts follow the AdGenerationSet lifecycle
Rationale: A second versioning scheme is debt; status + is_stale already
           solves staleness on channel reassignment
Affected Agents: Frontend Dev, QA
Action: Frontend Dev reads only active, non-stale sets
```

### 3. Question (Question / Consultation)
An agent asking a question of another agent or the user.

```
Backend Dev → Architect:
[QUESTION] How should per-workspace screening decisions be stored?
Options:
  A) New column on the existing candidate table
  B) Separate decision table keyed by (run_id, keyword_id)
My Preference: B — keeps the candidate table's contract stable and
               survives re-runs without destroying history
Response Expected: Yes
```

### 4. Report (Status Report)
An agent reporting completed work.

```
Frontend Dev → PM:
[REPORT] TASK-005 completed
Status: Ready for review
Files Created:
  - frontend/src/pages/Relevance.tsx
  - frontend/src/services/api.ts
  - frontend/src/pages/Relevance.test.tsx
Notes: lint/format/build/test all green; verified in browser after container restart
Next Step: Awaiting QA review
```

### 5. Blocker (Blocking Issue)
An agent reporting that it cannot proceed.

```
Frontend Dev → PM:
[BLOCKER] TASK-007 blocked
Reason: the relevance endpoint does not exist yet
Dependency: TASK-006 (Backend Dev)
Impact: the Relevance page cannot be completed
Workaround: none that is honest — a mocked page would look done and is not
```

### 6. Review Request (Review Request)
A request for code review or architectural review.

```
PM → QA:
[REVIEW-REQUEST] TASK-005 ready for review
Agent: Frontend Dev
Files: [file list]
Focus Points:
  - Form validation logic
  - Error state handling
  - Loading/empty/error state coverage
Expected Output: Code review report
```

## Escalation Chain

If issues cannot be resolved, follow this order:

```
Dev Agent → PM → Architect → CEO → User
```

Any issue unresolved at one level is escalated to the next. However, the following are escalated directly to the user:
- Matters requiring scope changes
- Budget/cost decisions
- Design decisions dependent on user preference
- Security policy changes

## Conflict Resolution

If two agents disagree:

1. Both agents present their reasoning in writing
2. The PM evaluates the matter
3. If it is a technical matter, the Architect decides
4. If it is a strategic matter, the CEO decides
5. If still unresolved, it is presented to the user

## User Communication Rules

- Every message directed to the user must be clear and concise
- Keep technical jargon to a minimum (adjust to the user's level)
- Present at most 2-3 decision points at a time (avoid overwhelming)
- Give your recommendation, but leave the choice to the user
- Batch non-urgent matters and ask them together

Reply to the user in the language the user used.
