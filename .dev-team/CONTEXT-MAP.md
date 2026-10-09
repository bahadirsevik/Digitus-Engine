# Context Map

## Agent → access matrix

| Agent | Must read | Must write |
|-------|-----------|------------|
| CEO | CLAUDE.md, PROJECT, ROADMAP, STATE | PROJECT, ROADMAP |
| Architect | CLAUDE.md, ARCHITECTURE, STATE, relevant plan_*.md | ARCHITECTURE, decisions/ |
| PM | REQUIREMENTS, ROADMAP, STATE | REQUIREMENTS, ROADMAP, STATE |
| Frontend Dev | CLAUDE.md §7/§12, ARCHITECTURE, STATE, frontend/src/ | frontend files |
| Backend Dev | CLAUDE.md §4/§6/§7, ARCHITECTURE, STATE, app/ | backend files, migrations |
| QA | CLAUDE.md §8/§11, STATE, tests/ | test files |
| UI/UX | CLAUDE.md §12, frontend/src/styles/, STATE | design specs |
| Memo | all (read) | STATE, decisions/, sprints/ |

## Feature → responsible agents

| Feature | Agents |
|---------|--------|
| Scoring / channel assignment | Backend (+ Architect if the gate opens) |
| Workspace / brand profile | Backend + Frontend |
| Keyword import + dedup | Backend |
| Generation (SEO / ADS / Social) | Backend + QA (grounding rules) |
| Dashboard / export UI | UI/UX + Frontend |
| Screening / council experiments | Backend + Architect + Council |
