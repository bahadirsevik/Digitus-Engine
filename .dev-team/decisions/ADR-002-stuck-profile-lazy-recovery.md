# ADR-002 — Stuck profile recovery: read-time (lazy) staleness

Date: 2026-09-08 · Status: **implemented, shipped as-is (user ruling: Option A)**
Agents: Backend Dev (implement), QA (static review). Gate CLOSED — Architect and
Council did not run: the pattern is already recorded in this repo.

## Context

`CLAUDE.md` §4/§10 records "site profile analysis runs in FastAPI BackgroundTasks;
a restart leaves profiles stuck in pending/running". That note is **stale**:
`app/core/site_analyzer/stuck_janitor.py` already exists, is wired into the
`app/main.py` lifespan, and has 4 unit tests.

But it leaves the most common case open. The janitor runs **once, at startup**, and
deliberately skips rows younger than `PROFILE_STALE_MINUTES` (15). A profile that was
`running` at the instant of the restart is younger than the threshold at startup, is
skipped, and is never revisited — so it stays `running` forever and the UI polls it
forever. `tests/unit/test_profile_janitor.py::test_fresh_running_untouched` locks in
that skip and is correct as written; the gap is that nothing runs the check again.

## Decision

Resolve staleness **lazily, at read time**, the way this repo already does for
`PREVIEW_STALE_MINUTES` and `DISCOVERY_STALE_MINUTES` in `app/api/v1/brand_profile.py`.

- `stuck_janitor.fail_if_stuck(db, profile, *, stale_minutes)` — single-row check;
  no-op and no commit unless the row is in `STUCK_STATUSES` and past the threshold.
- The age rule (`updated_at`, falling back to `created_at`, naive→UTC-aware) is
  factored into one helper shared with the bulk `fail_stuck_profiles`, so the startup
  sweep and the read-time check cannot disagree about age.
- Called from `get_profile` (`GET /runs/{run_id}/profile`) and `get_workspace`
  (`GET /workspaces/{workspace_id}`) — the latter is what the frontend polls.

Explicitly **not** done: no move to Celery (a killed Celery worker reopens the same
hole; the real fix is resolving staleness on read), no schema change, no migration,
no new package.

## QA finding — RAISED, and the user ruled: accept the risk (Option A)

The startup janitor never competed with a live analysis (at startup the in-process
task is already dead). The read-time check **can**: an analysis still legitimately
running past 15 minutes is now shown as `failed` on the next poll. Two consequences:

1. A false "failed / retry" message on a genuinely slow run.
2. If the user then retries, `analyze_profile` has **no single-flight guard** — it
   always resets to `pending` and starts a second background run, while the first is
   still alive. `_run_profile_analysis` ends with an unconditional
   `status = "draft"` write, so the older run can clobber the newer one. That race is
   pre-existing (a double-click does it today); this change makes it easier to reach.

Bound on a real run: crawl is capped at `MAX_PAGES_PER_SITE = 5` × `REQUEST_TIMEOUT = 15s`
≈ 75s per site, plus AI extraction with retries, plus one crawl per competitor URL when
competitor validation is on. 15 minutes is unlikely but not structurally impossible.

Hardening was offered and **declined by the user on 2026-09-08 (Option A)**: the fix
ships as written and the residual risk is accepted. The declined option was a separate,
longer threshold for the read-time check plus a single-flight guard on `analyze_profile`
returning 409 while a fresh run is in flight — the row-lock + 409 pattern `CLAUDE.md` §12
already uses for ADS dispatch. If the false-"failed" message is ever reported in the
field, that is the change to make; nothing else needs revisiting first.

## Checks actually run

- `docker-compose -f docker-compose.test.yml run --rm test_app sh -c "alembic upgrade head && pytest tests/ -q"`
  → **3131 passed, 18 failed** in 325s. All 18 are in `tests/integration/test_a1_executor.py`,
  fail identically in isolation, and come from a `SourceSealError: ONAY BAGI DUSTU:
  ['price_snapshot']` in the `scripts/` A1 experiment path. That file imports nothing
  from `app.api` or `app.core.site_analyzer`, so it cannot be affected by this change —
  pre-existing, caused by drifted uncommitted artifacts.
- Targeted: `tests/unit/test_profile_janitor.py` + `tests/integration/test_profile_stuck_recovery.py`
  → **10 passed**.
- Frontend checks: **not run** — no frontend file changed.

## Uncommitted

Nothing committed. The change sits in the working tree alongside a large set of
unrelated pre-existing modifications.
