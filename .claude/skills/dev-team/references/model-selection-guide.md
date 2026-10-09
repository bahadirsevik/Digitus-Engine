# Model Selection Guide

> **SINGLE SOURCE OF TRUTH for class names.** Every other dev-team file uses the
> **class name** — `Apex` / `Frontier` / `Balanced` / `Cheap` — never a model id.
>
> **But this file is NOT the source of truth for prices or ids.** Those are
> resolved live (§1). The tables below are a **dated snapshot**, and a snapshot
> that is treated as current is exactly how this guide went wrong once already:
> on 2026-09-01 it was measured against the live source and was wrong on six
> counts (§0).

## 0. Correction log — why this file no longer freezes prices

**2026-09-01 audit.** The guide was checked against the live model/pricing
source and found actively misleading:

| It said | Reality |
|---|---|
| Frontier ≈ $15 / $75 | Opus 5 is **$5 / $25** |
| Cheap ≈ $0.80 / $4 | Haiku 4.5 is **$1 / $5** |
| — (absent) | **Fable 5 exists at $10 / $50, above Opus** — and it is the tier architecture work should route to |
| `claude-haiku-4-5-20251001` | The API does not want date suffixes: `claude-haiku-4-5` |
| Effort `none` / `minimal` | Those levels no longer exist: `low`→`max` |
| "Last updated 2026-08" implied current | Sonnet 5's intro pricing expired 2026-08-31, so even a month-old table was wrong |
| Frontier ≈ 19× Cheap | Real ratio is **5×** (Opus/Haiku); Apex/Cheap is **10×** |

**The economic consequence matters more than the numbers.** The old 19× gap
justified avoiding the top tier aggressively. At 5–10× the saving from avoiding
it is far smaller, while the cost of a wrong cheap answer (rework, or worse, a
silently wrong one) is unchanged. **Lean on the top tiers more readily than
this guide previously implied.**

Full audit with evidence: `~/codeway_apps/owlbot/ROLE-REVIEW-2026-09-01.md`.

## 1. Classes — and how ids/prices resolve at use time

| Class | Harness alias | Model (snapshot 2026-09-01) | Context |
|-------|---------------|----------------------------|---------|
| **Apex** | `fable` | `claude-fable-5` | 1M |
| **Frontier** | `opus` | `claude-opus-5` | 1M |
| **Balanced** | `sonnet` | `claude-sonnet-5` | 1M |
| **Cheap** | `haiku` | `claude-haiku-4-5` | **200K** |
| **None** | — | no model; deterministic work → script | — |

**Resolution rule — never write a versioned id into a prompt or a file:**

- **Inside Claude Code** (the normal case): pass the **harness alias** to the
  Agent tool's `model` parameter (`fable` / `opus` / `sonnet` / `haiku`). The
  harness resolves it to the current version. An alias cannot go stale.
- **Calling the API directly**: query `GET /v1/models` for ids and capabilities,
  and load the `claude-api` skill for current ids, prices and per-model API
  differences. Do not answer pricing from memory — that is what produced §0.

**Cheap's 200K context is a hard constraint, not a preference.** A repo-wide
sweep handed to Cheap silently reads a fraction of the input. Breadth work goes
to a 1M-context class regardless of how mechanical it looks.

## 1b. Discovering aliases — the list itself is not fixed either

**The objection that produced this section (user, 2026-09-01):** *"You say
aliases don't go stale — but when dev-team was first written `fable` did not
exist. A new alias we don't know about can appear."* Correct. An alias is
stable **once it exists**; the *set* of aliases grows.

So the alias set is discovered, never assumed:

- **Inside Claude Code** the live source is the **Agent tool's own `model`
  parameter enum**. It is in the tool schema every session, and a new tier
  appears there the day it ships. Read it; do not trust this file's list.
  (Snapshot 2026-09-01: `haiku`, `sonnet`, `opus`, `fable`.)
- **On the API side** `GET /v1/models` returns the live id list with
  `capabilities` and `max_input_tokens`.

**Ranking a newly discovered model is NOT automatic.** The models endpoint does
not return price, so nothing in the live data says "this new one is the top
tier". Therefore:

> **Detection is automatic; tier assignment is confirmed by a human, once.**
> When a model appears that the stored assignment does not cover, `owlbot
> doctor` surfaces it — *"new model available: X. Which class?"* — and refuses
> to guess. An unassigned model is simply not routed to; it is reported, not
> silently ignored.

This is the mechanism that would have caught Fable 5 on the day it shipped
instead of a month later.

## 1c. Effort mode — the user-facing knob above all of this

Class assignments (§4) say what a *task type* deserves. **Mode** says how
generous to be overall. User-settable, **default `mid`**:

| Mode | Apex (top) | Frontier / Balanced (mid) | Cheap (low) |
|------|-----------|---------------------------|-------------|
| `high` | Default for hard work — architecture, root-cause, critical review | The workhorse | Only trivially mechanical work |
| `mid` **(default)** | Only where §4/§6 demand it: architecture, irreversible calls | **Default for most work** | Narrow, deterministically checkable work |
| `low` | **Not used unless genuinely unavoidable — and the reason is stated out loud** | Default for hard work | Default |

The user changes mode at any time and the change applies from that moment; it
is never inferred from the phrasing of a request.

**The floor survives every mode.** Mode shifts *preferences*, never the
prohibitions in §2: no mode routes a check, parser, gate or verifier to Cheap;
no mode hands breadth work to a 200K context; no mode lets an agent take one of
the decisions it is not allowed to take. `low` mode buys cheaper work, **not
weaker verification** — otherwise it silently reintroduces the false-green
defect class (measured: 13 false greens in 80 recorded defects on the scanner
project, `~/codeway_apps/owlbot/ROLE-REVIEW-2026-09-01.md`).

When `low` mode forces a downgrade on work that §4 would have sent higher, say
so in the cost footer: *"mode=low: Balanced instead of Apex on the architecture
step."* A silent downgrade is the thing this table exists to prevent.

## 2. Class Profiles

### Apex (Fable tier)
**Strengths**: The hardest reasoning; long-horizon agentic work; architecture;
root-cause analysis; adversarial review where being wrong is expensive.
**Cost**: 10× Cheap.
**Use for**: genuinely ambiguous architecture, critical review, the one
judgement call that would be expensive to get wrong.

### Frontier (Opus tier)
**Strengths**: Deep reasoning and large-codebase analysis at half of Apex.
**Cost**: 5× Cheap.
**Use for**: hard work that is not the single most critical decision — the
default when Balanced has already failed, and the fallback when Apex is
unavailable.

### Balanced (Sonnet tier)
**Strengths**: Speed/quality/cost balance, strong coding, good instruction
following. **Cost**: 3× Cheap.
**Use for**: **the default workhorse** — feature development, tests, docs.

### Cheap (Haiku tier)
**Strengths**: Very fast, 1× cost, fine for structured narrow tasks.
**Weaknesses**: Weak on open-ended reasoning; **200K context**.
**Use for**: classification, extraction, formatting, record keeping — where the
output is deterministically checkable and failure is loud.

**Never route to Cheap:** anything that inspects, greps, parses, gates or
verifies. A cheap model writing a *check* produces plausible commands that
no-op green — measured four times in one session on the scanner project. When
the failure mode is silence, the cheapest tier is a false economy.

## 3. Effort — the second axis

Every assignment is a pair of **class + effort**:
`low | medium | high | xhigh | max` (default `high`).

`xhigh` is the recommended setting for coding and agentic work on the Apex and
Frontier tiers. `max` is for the irreversible call where correctness outweighs
cost. `low` is for narrow sub-agent tasks.

**Key principle, with the corrected arithmetic:** a smaller model at higher
effort often beats a larger model at lower effort on quality. But the cost gap
is **smaller than this guide used to claim** — Balanced is 60% of Frontier's
price, not 25%. So choose the smaller model for *quality* reasons when it holds,
not because the saving is dramatic; and when the task is one where being wrong
is expensive, buy the higher tier.

## 4. Agent → Class + Effort Assignment

| Agent | Class | Effort | Reason |
|-------|-------|--------|--------|
| 🌐 Translator | **Cheap** | `low` | Runs on every message, cost is critical. Detection via script first (§5) |
| 📝 Memo | **Cheap** | `low` | Template writing, no reasoning required |
| 👔 CEO | **Balanced** | `medium` | Routes work; the depth comes from the Architect |
| 🏗️ System Architect | **Frontier** | `medium` | Genuine architecture decisions. **Runs conditionally** (§6). Apex / effort >`medium` are **opt-in only** — ask the user (ruling 2026-09-08, budget) |
| 📋 PM | **Balanced** | `low` | Task breakdown, dependency graph — structured work |
| 💻 Frontend Dev | **Balanced** | `low` | Routine React/TS. Ambiguous bug or refactor → `high` |
| 🔧 Backend Dev | **Balanced** | `low` | Routine FastAPI/SQLAlchemy. Security, migration or schema design → `high` |
| 🎨 UI/UX | **Balanced** | `low` | Design decisions within a known design system |
| 🧪 QA | **Balanced** | `low` | Standard review. Critical or security review → `high`, and **Frontier** when the reviewer would otherwise be the author |

### Effort Escalation Rule
An agent starts at `low` and moves to `high` when:
- The same task failed Plan-Check twice
- It changes 3 or more files at once
- It is investigating a bug whose root cause is unknown
- There is a security or data-loss risk

### Escalation Ladder
```
Cheap+low → fails twice → Balanced+low → fails
→ Balanced+high → still failing → Frontier+high → [ASK THE USER] → Apex+xhigh
```
No escalation without evidence. But do not treat the ladder as a reason to
start low on work whose failure would be silent — see §2's "never route to
Cheap" list, which is a floor, not a preference.

## 5. Translator — Script-First Language Detection

Because the Translator runs on every message, it is the largest hidden cost
item. **Never give language detection to a model.** The order is:

1. **Script/heuristic**: Turkish diacritics (`ğüşıöç`), common Turkish function
   words (`bir, için, ve, bu, ile, var, yok, ama, gibi`), ASCII ratio
2. If the result is **English** → the Translator is never called; the message
   passes straight through
3. If non-English → translate with Cheap + `low`
4. If the heuristic is undecided (mixed language) → Cheap + `medium`

Gain: translation cost drops to zero for English messages and roughly 50% for
mixed usage.

## 6. Architect — Conditional Execution

The Architect is the most expensive seat, so it is the single largest cost item.
**Do not run it on every task.** Put it behind this gate:

**Ruling 2026-09-08 (user, budget):** this seat is **Frontier (`opus`) + `medium`**, not Apex +
`xhigh`. No seat routes to Apex by default any more; Apex is opt-in and needs the user's word.

```
Is this a KNOWN pattern?
├─ YES (adding a screen/endpoint/model like the existing ones)
│   └─ SKIP the Architect → PM does task breakdown directly
│       (the decisions already in ARCHITECTURE.md are sufficient)
└─ NO
    ├─ Does it introduce new technology or a new package?
    ├─ Does the data model change?
    ├─ Does a contract between layers change?
    ├─ Is the decision expensive to reverse?
    └─ Any YES → run the Architect (Frontier + `medium`)
```

Also: the Architect's output must be a **decision or plan, not code**. Output
tokens cost **5× input tokens on every tier** (measured: the ratio is uniform),
so a top-tier model emitting long code is the most expensive thing in the
system. Balanced devs write the code.

Expected effect (estimate, not measured): the Architect runs in about 30% of
tasks.

## 7. Relative cost — the ratio is what the design rests on

Absolute prices change; the **ratio** drives the routing decisions. Measured
2026-09-01, and note that input and output ratios are identical:

| Class | Relative cost | Input $/1M | Output $/1M |
|-------|---------------|------------|-------------|
| Apex | 10× | $10 | $50 |
| Frontier | 5× | $5 | $25 |
| Balanced | 3× | $3 | $15 |
| Cheap | 1× | $1 | $5 |

### Cost Formula
```
cost = (input_tokens / 1M × input_price) + (output_tokens / 1M × output_price)
```

### Consequences (recomputed — the old numbers were wrong)
- Apex → Balanced saves **70%**; Apex → Cheap saves **90%**
- Frontier → Balanced saves **40%** (the old guide claimed 80%)
- Frontier → Cheap saves **80%** (the old guide claimed 95%)
- Output is **5× input on every tier** — verbosity costs more than tier choice
  in many chains. Shortening output is often the cheaper optimisation.

### Refreshing this table
Load the `claude-api` skill (it carries the current table and a live lookup
path) or query `GET /v1/models`. Then update §7 **and** the date in §1. If you
cannot verify, write "unverified" rather than leaving a stale number standing —
a number nobody checked is the failure recorded in §0.

## 8. Prompt Caching — Mandatory Optimization

dev-team's SKILL.md and reference files enter context on every trigger. That
stable content is cacheable:

- **Cache write**: ~25% extra on the first write
- **Cache hit**: ~90% off on subsequent reads
- **Batch API**: ~50% off for non-urgent work

### Design Rule — Stable First, Variable Last
Build every agent prompt in this order:

```
1. [STABLE / cacheable]  Agent definition (agents/*.md)
2. [STABLE / cacheable]  Relevant reference files
3. [SEMI-STABLE]         ARCHITECTURE.md, REQUIREMENTS.md
4. [VARIABLE]            STATE.md current status
5. [VARIABLE]            The task itself
```

Never lead with variable content; it invalidates the cache.

### Length is a decision, not a limit (user rule, 2026-09-01)

There is **no line ceiling.** A checklist chopped up to hit a number is a
checklist the agent half-reads, which is worse than a long one. Write what the
job needs — and past roughly **250 lines, carry the reason in the front matter**
so the one question worth asking gets asked: *is this one document, or two
wearing one name?*

What still holds is the **caching** discipline, which is about *where* content
sits, not how much of it there is: keep the always-loaded entry file focused and
push detail into `references/`, read **only when needed**. A document that is
not read on every trigger costs nothing.

(This replaces an earlier "< 350 lines" rule that no one had decided and that
this skill's own entry file already violated.)

## 9. Cost Reporting

Footer under every agent output:
```
⚙️ Agent: [emoji + name] | Class: [class] | Effort: [level] | Tokens: ~XK in / ~YK out | Cost: ~$Z
```

Report the dollar figure **only from prices verified this session**; otherwise
report tokens alone. A cost line computed from a stale table is worse than no
cost line, because it looks measured.

Always report when the Architect gate skipped the Frontier step — that is the main
saving and the user should see it.

## 10. Anti-Patterns

- ❌ **Freezing a price or model table and treating it as current** — the §0 failure
- ❌ Writing a versioned model id (or a date-suffixed one) into a prompt or file
- ❌ Reporting a dollar cost from an unverified price
- ❌ Handing breadth work (repo-wide sweep) to a 200K-context class
- ❌ Routing a check, parser or gate to Cheap — failure there is silent
- ❌ "Use the top tier to be safe" — escalation without evidence
- ❌ An expensive model on a step that runs on every message
- ❌ Asking a model to detect language
- ❌ Running the Architect on every task
- ❌ Making a top-tier model emit long code output
- ❌ Putting variable content at the start of the prompt and killing the cache

Reply to the user in the language the user used.
