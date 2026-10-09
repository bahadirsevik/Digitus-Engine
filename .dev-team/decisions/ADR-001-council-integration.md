# ADR-001: Cross-vendor council as the second opinion on architecture and review

**Date:** 2026-09-08
**Status:** Accepted
**Decided by:** User (K1–K4 plus the mode ruling), implemented by Claude

## Context

dev-team's nine agents are all Claude. When they agree, the agreement carries no
independent information — it is one set of priors, restated. The project's own history
shows the cost of that: conclusions that survived on post-hoc exceptions, and a measured
class of "false green" checks.

The user holds Codex and Gemini subscriptions and a DeepSeek API key, and asked for more
than one opinion on final decisions.

## Decision

Install `claude-council` and wire it to dev-team's Architect Gate.

1. **The gate governs both.** Closed (known pattern, decision already in `CLAUDE.md`) →
   neither the Architect nor the council runs. Open → both run.
2. **Architecture decisions use blind mode.** The council is asked the same question
   *without* being shown the Architect's answer; the two are compared afterwards.
3. **Code review uses review mode.** Material goes to the council as material to evaluate,
   never labelled as "our proposal". The synthesis reports disagreement; it does not
   resolve it.
4. **The council never decides.** It is evidence for the user's decision.
5. **Claude does not take a seat.** It is the chair — it distributes and synthesises.
   A Claude seat (available via OpenRouter, or via `agy`'s Claude models) would add
   shared priors, not corroboration.

## Rationale

Blind mode exists because of a measured failure, not a theoretical one. In the 2026-09-08
calibration, an imprecise framing in one of the three questions sent **all three seats**
down the same wrong path — and none of them flagged the ambiguity, although the prompt
explicitly invited them to reject the premise. The council is not three independent minds;
it is three minds given the same prompt. Blind asking is the only mechanism that limits a
shared framing error.

The chair/seat distinction follows the same logic, plus the council's own warning about its
`--local` mode: when every member is Claude, agreement reflects shared training.

## Alternatives considered

- **Claude as a fourth seat** — rejected: no vendor diversity, and it costs cash via
  OpenRouter.
- **Council on every task** — rejected: most tasks are known patterns whose answer is
  already in `CLAUDE.md`; a call that rediscovers a recorded decision is waste.
- **Unguarded CLI seats** (letting Codex read the repo) — the user's first preference, so
  that the seats could reason from the code. Reversed after measurement: Codex read `.env`
  successfully even with the workspace root narrowed to `app/`, because `-s read-only`
  blocks writes, not reads. Chosen instead: guard all seats, feed context via `--file=`.
  This also made the three seats symmetric, so a disagreement reflects judgement rather
  than one seat having read more than the others.

## Consequences

- The council knows only what the prompt carries → **framing quality is now a
  first-class concern**, and paraphrase is not acceptable where an artefact exists.
- Two of three seats cost no cash; DeepSeek does. Cost is reported per call, and reported
  as tokens alone when usage was not captured.
- `.env` is protected by a prompt-level guard, not a sandbox boundary. The structural fix
  remains open (see `STATE.md`).

## Calibration record

| Question | Result |
|----------|--------|
| S1 — known-answer diagnosis (competition scale error) | 2/3. `codex` proposed a hypothesis that could not explain the data it was given, then rationalised the contradiction rather than revising |
| S2 — closed decision (where is the bottleneck) | 3/3, and all three independently proposed the same next experiment |
| S3 — open question (relevance discrimination) | Structural conclusions sound and independently corroborated a prior decision; mechanism answers unusable because the framing was wrong |
