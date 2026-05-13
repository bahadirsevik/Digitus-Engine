# AGENTS.md

Codex icin proje talimatlari. Daha genis mimari dokumantasyon icin `CLAUDE.md` dosyasina bak.

## Project Summary

Digitus Engine V2, AI destekli anahtar kelime analizi ve kanal atama motorudur. CSV ile yuklenen keyword'leri ADS, SEO ve SOCIAL kanallari icin skorlar, Gemini AI ile niyet analizi yapar, kanal havuzlari olusturur, icerik uretir ve sonuclari DOCX/PDF/Excel/CSV olarak export eder.

Hedef kullanici: dijital pazarlama ajanslari ve SEO/SEM uzmanlari.

## Stack

- Backend: Python 3.11, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Redis, Celery, Pydantic, Loguru, Gemini SDK.
- Frontend: React 18, TypeScript, Vite, React Router, Zustand, Axios, Lucide React.
- Runtime: Docker Compose.

## Important Paths

- Backend entry point: `app/main.py`
- API routers: `app/api/v1/`
- Business logic: `app/core/`
- Scoring modules: `app/core/scoring/`
- Channel assignment pipeline: `app/core/channel/`
- DB models and CRUD: `app/database/`
- Content generators: `app/generators/`
- Compliance checks: `app/compliance/`
- Exporters: `app/exporters/`
- Celery tasks: `app/tasks/`
- Schemas: `app/schemas/`
- Frontend entry point: `frontend/src/main.tsx`
- Frontend pages/components/services/stores: `frontend/src/`
- Alembic migrations: `migrations/`
- Tests: `tests/`

## Common Commands

```bash
# Start all Docker services
docker-compose up -d

# Backend dev server
uvicorn app.main:app --reload

# Celery worker
celery -A app.tasks.celery_app worker --loglevel=info

# Celery beat
celery -A app.tasks.celery_app beat --loglevel=info

# Frontend dev server
cd frontend && npm run dev

# Run all tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ --cov=app --cov-report=html

# Unit tests
pytest tests/unit/ -v

# Integration tests
pytest tests/integration/ -v

# Create and apply migrations
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Architecture Notes

- The app follows a layered architecture:
  - `api/`: HTTP routers and validation.
  - `core/`: domain/business logic.
  - `database/`: models, CRUD, connection.
  - `generators/`: AI content generation.
  - `tasks/`: async Celery workflows.
- Channel assignment pipeline:
  candidate pool -> AI intent analysis -> AI prefilter -> final selection -> backfill.
- Cross-channel transfer exists: ADS-rejected keywords can move to SEO.
- AI fallback behavior matters: if AI fails, channel-specific default intent is assigned.
- AI calls are batched in groups of 6 to reduce JSON truncation risk.
- Turkish fuzzy dedup depends on character normalization and an 85% similarity threshold.

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
- Keep Celery tasks under `app/tasks/`.
- Shared constants currently live in `app/core/constants.py`.

## Do Not Touch Lightly

- Do not edit existing files under `migrations/versions/`; add a new migration when needed.
- Do not change scoring formula coefficients in `app/core/constants.py` casually; these are business decisions.
- Do not edit `.env`; it contains sensitive local values.
- If `app/database/models.py` changes, check whether an Alembic migration is required.
- If adding a new Celery task module, add it to the `include` list in `app/tasks/celery_app.py`.

## High-Risk Areas That Need Tests

- `app/core/scoring/`: formula changes affect business output.
- `app/core/channel/channel_engine.py`: pipeline steps are interdependent.
- `app/core/channel/intent_analyzer.py`: AI JSON parsing robustness is critical.
- `app/database/models.py`: model changes usually require migrations.
- Gemini prompt templates: downstream code expects JSON output.

## Prompt Template Rules

- Prompt templates are mostly in `prompt_templates.py` files under generator modules.
- Some inline prompts exist in `app/core/channel/intent_analyzer.py` and `app/api/v1/generation.py`.
- Prompt outputs are expected to be JSON; preserve parseable shape.
- Preserve Python f-string escaping with `{{` and `}}` when literal braces are needed.
- Google Ads RSA limits are business rules: headline <= 30 chars, description <= 90 chars.

## Known Technical Debt

- CORS currently allows all origins and must be restricted for production.
- Export status uses an in-memory dict; production should move this to Redis or DB.
- CI/CD is not configured.
- Formatter/linter config is not standardized yet; ruff/black/isort were suggested.
- Static `sqlalchemy.url` in `alembic.ini` is overridden by `migrations/env.py`.

## Data Flow

```text
CSV Upload -> Dedup -> Scoring Run -> Score Calculation -> Candidate Pool ->
Intent Analysis (AI) -> Prefilter (AI) -> Final Pool + Backfill ->
Content Generation (AI) -> Compliance Check -> Export
```

## Codex Working Rules

- Read this file and relevant local code before making changes.
- Prefer existing patterns over new abstractions.
- Keep edits scoped to the requested behavior.
- Do not revert user changes.
- Add or run focused tests when touching high-risk areas.
- If tests cannot be run, explain why in the final response.
