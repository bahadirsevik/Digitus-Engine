# AGENTS.md

Codex icin proje hafizasi ve calisma talimatlari. Bu dosya repo taranarak guncellenmistir; eski/arsiv kaynaklar yerine aktif kodu esas al.

## Project Summary

Digitus Engine V2, dijital pazarlama ajanslari ve SEO/SEM ekipleri icin AI destekli keyword analiz, kanal atama, icerik uretim ve export motorudur.

Sistem CSV, JSON import, Google Ads keyword ideas veya kampanya keyword importu ile gelen kelimeleri workspace/marka bazinda tutar; ADS, SEO ve SOCIAL icin skorlar; Gemini ile niyet ve kanal uygunlugu analizi yapar; final havuzlar olusturur; SEO+GEO blog, Google Ads RSA ve sosyal medya icerikleri uretir; DOCX/PDF/XLSX/CSV olarak raporlar.

Ana hedef: keyword listesini uygulanabilir reklam, SEO ve sosyal medya planina cevirmek.

## Stack

- Backend: Python 3.11, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Redis, Celery, Pydantic v2, Loguru.
- AI: `google-generativeai` Gemini SDK; text generation, JSON responses, embedding relevance.
- Google Ads: `google-ads` SDK; keyword ideas, URL seed, campaign keyword import, trend calculation.
- Frontend: React 18, TypeScript, Vite, React Router, Zustand, Axios, Lucide React.
- Tests: pytest + real Postgres via `docker-compose.test.yml`; frontend Vitest + Testing Library.
- Runtime: Docker Compose. Main services: `app`, `db`, `redis`, `celery_worker`, `celery_beat`, `frontend`; optional profile service: `site_fetch_smoke`.

## Important Paths

- Backend entry point: `app/main.py`
- Settings/security: `app/config.py`, `app/core/security.py`
- API routers: `app/api/v1/`
- Router aggregator: `app/api/v1/router.py`
- Business logic: `app/core/`
- Scoring modules: `app/core/scoring/`
- Channel assignment pipeline: `app/core/channel/`
- Channel prefilters: `app/core/channel/pre_filters/`
- CSV/Google Ads import parser: `app/core/csv_import/`
- Site analyzer/relevance: `app/core/site_analyzer/`
- Dashboard summary/next action: `app/core/dashboard/`
- DB models and CRUD: `app/database/`
- Google Ads integration: `app/integrations/google_ads/`
- Content generators: `app/generators/`
- Compliance checks: `app/compliance/`
- Exporters: `app/exporters/`
- Celery tasks: `app/tasks/`
- Schemas: `app/schemas/`
- Frontend entry point: `frontend/src/main.tsx`
- Frontend pages/components/services/stores: `frontend/src/`
- Active Alembic migrations: `migrations/versions/`
- Archived old migrations: `migrations/versions/_archived/`
- Tests: `tests/unit/`, `tests/integration/`
- Old copy/reference only, not active source: `old_working/`

## Common Commands

```bash
# Start all Docker services
docker-compose up -d

# Backend dev server
uvicorn app.main:app --reload

# Celery worker
# NOT: Task imzasi/parametresi degistiyse celery_worker restart SART
# (once task_results'ta running gorev kontrolu) — eski worker yeni cagriyi patlatir.
celery -A app.tasks.celery_app worker --loglevel=info

# Celery beat
celery -A app.tasks.celery_app beat --loglevel=info

# Frontend dev server
cd frontend && npm run dev

# Frontend checks
cd frontend && npm run lint && npm run format:check && npm run build && npm run test

# Run all backend tests - ALWAYS via isolated test DB.
# NEVER run pytest inside digitus_app or against prod/dev DATABASE_URL:
# tests/conftest.py truncates tables per test. Use this command:
docker-compose -f docker-compose.test.yml run --rm test_app pytest tests/ -v

# Coverage
docker-compose -f docker-compose.test.yml run --rm test_app pytest tests/ --cov=app --cov-report=html

# Unit / integration only
docker-compose -f docker-compose.test.yml run --rm test_app pytest tests/unit/ -v
docker-compose -f docker-compose.test.yml run --rm test_app pytest tests/integration/ -v

# Create and apply migrations
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Architecture Notes

- Layered architecture:
  - `api/`: HTTP routers, validation, task triggering.
  - `core/`: domain logic: scoring, channel, workspace, csv import, dashboard, site analyzer.
  - `database/`: SQLAlchemy models, CRUD, connection.
  - `integrations/`: external service adapters, mainly Google Ads.
  - `generators/`: AI content generation.
  - `compliance/`: SEO/GEO checks.
  - `exporters/`: report builders.
  - `tasks/`: Celery workflows.
- Workspace isolation is central. Most user-facing data hangs from `BrandProfile` / `brand_profile_id`; cross-workspace access should return 404/400 through helpers such as `verify_scoring_run`.
- API key auth is opt-in in development: if `API_KEY` is set, all `/api/v1/*` routes require `X-API-Key`; production requires API key and non-wildcard CORS.
- Export is DB-backed via `ExportJob` and Celery `run_export_task`; do not describe it as only in-memory.
- Dashboard and Google Ads URL seed use Redis cache; cache failures are non-fatal.

## Current Production Engine (v3 — locked ADS/SEO/SOCIAL algorithms)

- Yeni run'larda ürün varsayılanı `algorithm_version="v3"`dür.
- Üretim portları `app/core/engine/` altındadır; ADS, SEO ve SOCIAL kilit/golden
  parity testleriyle korunur.
- v3 `execute` evreni `KeywordScore.metrics_snapshot` içine dondurur ve aynı
  görevde Family (gerekiyorsa) + seçili kanal motorları + post-policy teslimini
  çalıştırır. Ayrı bir channel-assignment kullanıcı adımı yoktur.
- Üretimde her LLM aşaması tek geçiştir; yalnız yapısal/teknik sözleşmenin izin
  verdiği sınırlı retry uygulanır. Benchmark'lardaki üç tekrar üretime taşınmaz.
- Final teslim `EngineSelection` ve `ChannelPool` üzerinden yapılır; post-policy
  geri doldurma veya ikinci AI kararı yapmaz.
- v2 ve v2_1 geriye uyumluluk için aktiftir. Aşağıdaki formüller yalnız bu
  legacy run'ları tarif eder; v3 algoritması olarak okunmamalıdır.

## Legacy Scoring Truth (v2 — list-relative additive formulas)

Do not use the OLD multiplicative formulas (sqrt/log10 * trend / competition);
they were replaced by Scoring v2 (`Digitus_Engine_v2_Skorlama_Algoritmalari.md`).
Current scoring is in `app/core/scoring/`.

- Derived variables are RELATIVE TO THE SCORED LIST (H/MB are average-rank
  percentiles, Ln is min-max log). Scores are NOT comparable across runs and
  must NEVER be cached.
- ADS (ROI Hunter): `N_ADS = 40*H - 15*R_N + 5*min(max(T3,0),1)` (~[-15,45]).
- SEO (Opportunity Engine): base `N_SEO = 40*Ln + 3*max(TrK,-0.5)`; at
  SELECTION time `+ 15*G_T + 4*G_A` (AI intent grades, `IntentAnalysis.gt/ga`).
  There is deliberately NO competition term (advertiser competition != organic
  difficulty).
- SOCIAL (Hype Tracker): `N_SOC = 20*H + 50*MB`, `MB = percentile(V*max(T3,0))`;
  T12 deliberately excluded.
- Pre-cleaning: rows with null/0 volume are DROPPED (`skipped_invalid_metrics`
  reported); DB trends are percent -> converted to /100 ratio and clamped to
  [-1,+3]; null competition -> 0.
- `KeywordScore.metrics_snapshot` scale contract: top-level trend_3m/12m are
  RAW PERCENT; `derived.t3/t12` are ratio+clamped; `derived` carries h/ln/trk/mb
  (final selection reads MB/H from here).

## Legacy Channel Assignment Truth (v2 — class-priority selection, NO backfill)

- Pipeline: candidate pool -> intent analysis (AI, parallel) -> brand exclusion
  -> channel prefilter -> class-priority final pool -> capacity-driven
  expansion -> ADS-to-SEO transfer with REAL intent -> SEO metadata step
  (post-selection). BACKFILL WAS REMOVED: eliminated (class -1) keywords can
  never enter the final pool; capacity filling is expansion rounds only.
- Per-channel AI authority DIFFERS:
  - ADS: AI classifies AND eliminates (hot_sale=2 / lead=1 / eliminate=-1 ->
    `PreFilterResult.ai_class`).
  - SEO: selection is FULLY DETERMINISTIC — AI cannot eliminate. The only SEO
    eliminations are brand exclusion + the deterministic price/fiyat filter
    (which runs in `_pre_brand_defense_filter` BEFORE brand-defense peel, so
    own-brand "fiyat" queries are also blocked; it works even with zero AI
    budget). Everything else keeps with `reason_code=SEO_KEEP_DEFAULT`,
    label=None, 0 AI calls. SEO metadata (h1/h2, depth_label) is generated
    AFTER selection by `generate_metadata` and can never change `is_kept`.
  - SOCIAL: AI scores 3 binary dims (class 0-3); CODE decides elimination
    (class 0 AND MB<=0.3).
- Final ordering is deterministic: `(-class, -adjusted, -volume, keyword)`.
- Expansion is CAPACITY-DRIVEN (the 70% fill threshold was removed). Stop
  reasons are STOP_* constants: capacity reached / candidate cap / AI budget /
  max 2 rounds / no more candidates. `unfilled_count` is reported explicitly.
- Expansion AI budget is HARD: per-channel `AiCallBudget` objects
  (`try_consume()` before every `complete_json`), total <= 60
  (`MAX_EXPANSION_AI_BATCHES`). Every AI layer returns a real `ai_calls_used`
  counter (early returns report 0); `batches` is display-only.
- AI output contract: intent + prefilters use Gemini `response_schema`
  structured output, 3000 max tokens, `_safe_parse_json` recovery chain as
  safety net. Missing-ID targeted retry + single-keyword final pass capped at
  `SINGLE_RETRY_MAX_PER_STAGE=10` per call, priority `(rank_in_channel,
  keyword_id)`. Parse errors get 1 whole-batch retry. Fallback results are
  marked `IntentAnalysis.source='fallback'` (gt/ga stay NULL); real AI results
  are `source='ai'`, transfers `source='transfer_ai'`.
- Cross-channel transfer (ADS->SEO): only NEWLY created candidates get real
  intent analysis (`source='transfer_ai'`); existing SEO prefilter rows are
  never overwritten; the price filter applies to transfers too. The return
  dict has separate denominators: requested/created/already_present/
  intent_ai_created/intent_fallback_created/price_blocked_created.
- `selection_quality` (Faz G): collected right before the channel_assigned
  transition into task result_data — per-channel capacity/final/unfilled,
  intent_fallback, prefilter_fallback/eliminated, brand_excluded, SEO
  price_blocked/metadata_unavailable/gt_ga_null_in_pool, ADS hot_sale/lead,
  plus transfers and expansion summaries.
- Dual-channel labels: ADS∩SEO -> `is_strategic`; SOCIAL top 20% + H>=0.7 ->
  `pool_label='rising_opportunity'`.
- Relevance: raw `KeywordScore` stays a pure document score; relevance only
  affects pool ordering via `adjusted_score = max(score,0) * relevance *
  coefficient` (negative-score inversion guard). Coefficient range 0.1-3.0.
- Batch sizes: intent 8 (`INTENT_BATCH_SIZE`), prefilter 6, brand filter 5,
  SEO metadata 4.

## Import, Dedup, and Relevance

- CSV import preview/commit logic lives under `app/api/v1/keywords.py` and `app/core/csv_import/`.
- Google Ads CSV parser handles Google Ads export quirks, UTF-16, header detection and import planning.
- Turkish fuzzy dedup lives in `app/core/keyword_dedup.py`.
- Dedup logic lowercases, strips one common Turkish suffix, normalizes Turkish characters, and uses fuzzy threshold 85.
- Fuzzy matches only merge when meaningful metrics match; fallback is exact dedup if fuzzy libs are unavailable.
- Site analyzer uses SSRF-protected crawling, profile extraction, anchor building, and Gemini embedding relevance.
- Embedding relevance uses cosine similarity, 768 dimensions, batch size 50, Redis cache TTL 7 days, fallback score 0.5.

## Content Generation and Compliance

- SEO+GEO generation: `app/generators/seo_geo/`
- Ads RSA generation: `app/generators/ads/`
- Social 3-phase generation: categories -> ideas -> contents in `app/generators/social/`
- Google Ads RSA limits are hard business rules: headline <= 30 chars, description <= 90 chars.
- ADS validators may keep, shorten, regenerate, truncate, or eliminate invalid assets.

### ADS Generation Versioning (AdGenerationSet — Faz E)

- Every RSA generation produces an `AdGenerationSet`
  (`generating|active|draft|archived|failed` + `is_stale`); `AdGroup` rows are
  NOT NULL linked via `generation_set_id` and derive `scoring_run_id` from the
  set. Lifecycle module: `app/generators/ads/generation_sets.py`.
- Dispatch (`_dispatch_ads` in `app/api/v1/generation.py`) serializes via a
  ScoringRun row lock: dual guard (active ads TaskResult + generating set),
  version allocated under lock, second request gets 409. Stale reconciliation
  (task older than `ADS_TASK_STALE_SECONDS=1200`) is STATUS-BASED: generating
  set -> both failed; active/draft/archived set -> set kept, TaskResult
  repaired to completed; orphan generating sets time out via created_at.
- Finals are atomic: AdGroups + set status + TaskResult final state commit in
  ONE transaction (`finalize_ads_success/failure`); `update_task_status` (own
  session) must never write finals. Worker verifies the set is still
  'generating' before AI work and before writing results.
- First successful set of a run auto-activates; later ones are `draft` until
  the user approves via `POST /generation/ads/sets/{id}/activate` (archive ->
  flush -> activate to respect the partial unique active index).
- TWO stale concepts: task-timeout staleness -> `failed`; content staleness
  (channel reassignment marks all run sets `is_stale=True`, status preserved).
  Consumers (RSA GET, dashboard, export) only read `status='active' AND
  is_stale=False` via `active_ad_groups()`. Regenerate from a stale set -> 409.
- Group regenerate is ASYNC (202): the source set is cloned into a new
  generating draft, only the target group is re-generated; history never mutates.

### Content Grounding (Faz F)

- Prompts (RSA + SOCIAL) contain İDDİA KURALLARI blocks and `{product_facts}`
  from the confirmed profile (`load_product_definition`); few-shots are
  claim-free. Do not reintroduce claim-bearing examples ("100.000+ Müşteri",
  "%80'i bu hatayı yapıyor").
- `find_ungrounded_claims(text, grounding_facts, exempt_keywords)` in
  `app/generators/ads/validators.py` deterministically flags numeric claims
  (Turkish normalization: "50 bin", "yüzde 80", "4.8/5 puan"), superlatives and
  earnings guarantees (always banned). Non-numeric claims are out of scope by
  design. Keyword exemption is narrow (exact keyword text only).
- Grounding whitelist = confirmed product_facts + the USP the user explicitly
  sent (`trusted_brand_usp`); the generic fallback USP is never whitelisted.
- ADS chain: every headline/description checked post-validation; one
  CLAIM_REWRITE attempt; unresolved assets eliminated; deterministic fallback
  assets are checked too; below-minimum groups FAIL.
- SOCIAL: caption+hooks checked; one regenerate chance; still-violating content
  is NOT saved and is reported as `warnings` ({idea_id, reason_code, claims})
  in responses and task result_data; all-rejected -> task failed with warnings
  preserved; regenerate never overwrites clean content.
- SEO compliance V2 checks 11 programmatic criteria, including title keyword, URL keyword, intro keyword count, 300-450 words, subheading count, links, bullets, readability.
- GEO compliance V2 checks 7 AI-oriented criteria, including direct answer, snippet extractability, hierarchy, informative tone, no fluff, first-50-word answer, verifiable info.

## Database and Migrations

- Active migrations start at `20260511_001_baseline_squash.py` (live
  `Base.metadata.create_all` — on fresh installs new columns already exist in
  the baseline, so EVERY additive migration MUST be idempotent with
  existence checks; pattern: `20260712_001` / `20260716_001`). The newest is
  `20260716_001_add_ad_generation_sets.py` (AdGenerationSet + legacy backfill).
- Older migrations live under `migrations/versions/_archived/`; do not edit archived or existing active migrations casually.
- If `app/database/models.py` changes, check whether a new Alembic migration is required.
- `migrations/env.py` overrides the static `sqlalchemy.url` in `alembic.ini`.

## Coding Conventions

- Python files, functions, variables: `snake_case`.
- Classes: `PascalCase`.
- Constants: `UPPER_SNAKE_CASE`.
- API URLs: kebab-case, for example `/cleanup-duplicates` and `/seo-geo`.
- Frontend components: `PascalCase`.
- Frontend variables/functions: `camelCase`.
- Keep API endpoints under `app/api/v1/`, one router per file.
- Keep business logic under `app/core/`, separated by domain.
- Keep Pydantic schemas under `app/schemas/`.
- Keep Celery tasks under `app/tasks/`; if adding a task module, add it to `include` in `app/tasks/celery_app.py`.
- Shared constants currently live in `app/core/constants.py`.
- Prefer existing patterns; do not introduce new architecture unless the local code clearly supports it.

## Do Not Touch Lightly

- `.env`; it contains sensitive local values.
- Existing files under `migrations/versions/`; add a new migration when needed.
- `migrations/versions/_archived/`; historical chain only.
- Scoring coefficients and channel thresholds in `app/core/constants.py`; these are business decisions.
- Prompt output shapes; downstream code expects parseable JSON.
- User/unrelated dirty worktree changes.
- `old_working/`; treat as archived reference, not active implementation.

## High-Risk Areas That Need Tests

- `app/core/scoring/`: formula changes affect business output.
- `app/core/channel/channel_engine.py`: pipeline steps are interdependent.
- `app/core/channel/intent_analyzer.py`: AI JSON parsing robustness is critical.
- `app/core/channel/pre_filters/`: labels, fallback and transfer behavior affect final pools.
- `app/core/csv_import/`: import filtering and Google Ads parser behavior affect data quality.
- `app/core/site_analyzer/`: SSRF guard, anchors and relevance can change ranking.
- `app/database/models.py`: model changes usually require migrations.
- `app/api/v1/brand_profile.py`: large router with workspace/profile/relevance side effects.
- Gemini prompt templates: downstream code expects JSON output.
- Exporters and content generators: broad user-facing output surface.

## Prompt Template Rules

- Prompt templates are mostly in `prompt_templates.py` files under generator modules.
- Additional prompts exist in `app/core/channel/intent_analyzer.py`, `app/core/channel/pre_filters/`, `app/core/channel/brand_filter.py`, `app/core/site_analyzer/profile_extractor.py`, and `app/api/v1/generation.py`.
- Prompt outputs are expected to be JSON; preserve parseable shape.
- Preserve Python f-string escaping with `{{` and `}}` when literal braces are needed.
- Keep Google Ads RSA limits: headline <= 30 chars, description <= 90 chars.

## Known Technical Debt / Watch Items

- CORS wildcard is allowed only outside production; production guard rejects it.
- Site profile analysis still uses FastAPI BackgroundTasks in places; restart can leave pending/running state stuck.
- Backend formatter/linter is not standardized; ruff/black/isort were suggested.
- CI/CD is not configured.
- `brand_profile.py` is large and contains a lot of orchestration.
- `.github/` is absent.
- `datas/`, `old_working/`, and plan/analysis markdown files exist; avoid treating them as active app code unless user asks.

## Codex Working Rules

- Read this file and relevant local code before making changes.
- For facts about current behavior, trust active code over older docs/plans.
- Prefer `rg` / `rg --files` for search.
- Prefer existing patterns over new abstractions.
- Keep edits scoped to the requested behavior.
- Avoid overengineering and endless review loops. Once a solution is safe and
  materially answers the user goal, stop refining it. Request another revision
  only for issues that can change the result, cause a security or data-integrity
  failure, create meaningful cost exposure, or prevent the workflow from
  running. Treat naming, reporting detail, artifact ceremony, theoretical edge
  cases, and measurement polish as follow-up work unless they are truly
  blocking. Prefer a small working experiment over another contract layer.
- Do not revert user changes.
- Add or run focused tests when touching high-risk areas.
- Backend tests must use `docker-compose.test.yml`; never run pytest against `digitus_app` or production/dev DB.
- If tests cannot be run, explain why in the final response.

## Data Flow

```text
Workspace/Profile -> CSV/JSON/Google Ads import -> Import preview/commit ->
Dedup/normalization -> ScoringRun -> ADS/SEO/SOCIAL score calculation (v2,
list-relative) -> Optional relevance compute/rerank -> Candidate pools ->
Intent analysis (AI; SEO gets gt/ga) -> Brand exclusion -> Channel prefilters
(ADS class, SEO deterministic, SOCIAL dims) -> Class-priority final pools +
capacity-driven expansion (NO backfill) -> ADS-to-SEO transfer (real intent) ->
SEO metadata step -> selection_quality -> AI content generation (grounded,
claim-validated; ADS via versioned AdGenerationSet) -> SEO/GEO/Ads validation ->
ExportJob -> DOCX/PDF/XLSX/CSV
```
