# Digitus Engine V2

## Vision
AI-assisted keyword analysis and channel-assignment engine: scores imported keywords
for ADS / SEO / SOCIAL, assigns them to channels, generates content per channel, and
exports the result.

## Target user
Digital marketing agencies and SEO/SEM specialists. Turkish-speaking domain experts,
not developers.

## Tech stack
Authoritative table: `CLAUDE.md` §2. Summary: FastAPI + SQLAlchemy 2.0 + Alembic +
PostgreSQL 15, Celery + Redis, React 18 + TypeScript + Vite, Gemini and DeepSeek.

## Constraints
- **Paid AI calls need explicit approval.** A run has cost real money before.
- pytest never runs against the app container or a production database.
- Multi-tenant by workspace; cross-workspace access returns 404.

## Success criteria
> NOT FILLED IN. These are the user's to state, and inventing them would be worse
> than leaving them blank. PM asks at the next planning session.
