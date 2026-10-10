# Architecture

> **CLAUDE.md (repo root) is the architecture of record.** This file does not
> duplicate it — it cites it, and records only decisions taken from 2026-09-08
> onward that are not yet in CLAUDE.md. If the two ever disagree, CLAUDE.md wins
> and this file is the one that is wrong.

## Where the authoritative content lives

| Topic | Source |
|-------|--------|
| Stack and versions | `CLAUDE.md` §2 |
| Folder/layer structure | `CLAUDE.md` §3 |
| Design decisions and **deliberate** exceptions | `CLAUDE.md` §4 |
| Database models, migration strategy | `CLAUDE.md` §6 |
| API surface | `CLAUDE.md` §7 |
| Test strategy and weak-coverage map | `CLAUDE.md` §8 |
| Known debt and traps | `CLAUDE.md` §10, §11 |

## Deliberate behaviours — do not "fix" these

Repeated here because they are the ones most often mistaken for bugs
(full reasoning in `CLAUDE.md` §4):

- SEO scoring carries **no competition term** — advertiser competition is not organic difficulty
- **AI does not eliminate in the SEO channel**; elimination there is deterministic
- **Backfill was removed**; an eliminated keyword cannot re-enter the final pool
- Relevance touches only `adjusted_score` in pool ordering, never the raw `KeywordScore`
- Redis failures are swallowed silently in cache paths

## Decisions taken from 2026-09-08 onward

See `decisions/`. Nothing recorded yet beyond ADR-001.
