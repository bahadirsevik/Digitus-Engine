# Council Integration — Cross-Vendor Second Opinion

dev-team's agents are all Claude. That is a single set of priors: when they agree, the agreement is not evidence. The council supplies the missing thing — models from *other* vendors, answering the same question independently.

This document says when the council runs, how it is asked, and what its answer is allowed to do.

Setup, seats and the security decisions behind them: `~/.claude/skills/claude-council/`, and the memory note `project-claude-council-kurulum`.

## 1. The Seats

| Seat | Model | Effort | Cost |
|------|-------|--------|------|
| `codex` | `gpt-5.6-sol` | medium | ChatGPT subscription — no cash |
| `antigravity` | `gemini-3.8-flash-high` | high | Google subscription — no cash |
| `deepseek` | `deepseek-v4-pro` | — | **cash** (the only paid seat) |

All three are **guarded**: they use no tools and read no files. This is deliberate — Codex's `-s read-only` sandbox blocks writes, not reads, so an unguarded seat could read `.env`. It also keeps the seats symmetric: a disagreement between them reflects judgement, not one seat having read more than another.

**Consequence: the council knows only what we put in the prompt.** Context reaches every seat the same way — `--file=`.

## 2. When the Council Runs

The council is gated on the **same condition as the Architect**. If the Architect Gate is closed (known pattern, decision already in `CLAUDE.md`), the council does not run either.

```
Architect Gate OPEN
   ├── 🏗️ Architect writes its decision  ─┐
   ├── 🏛️ Council answers the same         │  independently, in parallel,
   │      question BLIND (mode b)         │  neither seeing the other
   ├── Compare ────────────────────────────┘
   └── 🔔 Report to the user: where they agree, where they diverge, and why
```

It also runs, in mode (a), on:
- A code review where the reviewer would otherwise be the author
- A measurement result that is about to become a decision
- Any claim the user is being asked to approve that rests on an empirical premise

It does **not** run on: routine code, bug fixes, test writing, formatting, or anything whose answer is already written in `CLAUDE.md` or the memory index. Read those first — a council call that rediscovers a recorded decision is pure waste.

## 3. Two Modes

### (a) Review mode — for code and for decisions already made
The material goes to the council as **material to evaluate**, not as "our proposal". The synthesis **reports** the disagreement; it does not resolve it. The decision stays with the user.

```bash
bash ~/.claude/skills/claude-council/scripts/query-council.sh \
  --file=<path> -- "<the question>"
```

### (b) Blind mode — for architectural decisions
The council is asked the question **without** being shown the Architect's answer. Only afterwards are the two compared.

Why this mode exists, with evidence: in the 2026-09-08 calibration, an imprecise framing in one question sent **all three seats** down the same wrong path. The council is not three independent minds — it is three minds given the same prompt. A shared framing error is a shared answer error. Blind asking is the only mechanism that limits this.

## 4. How to Ask — the Part That Decides the Answer's Worth

**The council's value is bounded by the quality of the framing.** Measured, not assumed. So:

1. **Send artefacts, not paraphrase.** `--file=` the real report, the real diff, the real numbers. A paraphrase carries your reading of the evidence into their answer.
2. **State directions explicitly.** "Ratio of A to B" is ambiguous; "A's median is 14.4× B's median" is not. The calibration failure was exactly this.
3. **Invite refusal, and mean it.** Include: *"Say the premise is wrong if it is, and name what is ambiguous rather than assuming."* Note that in calibration this instruction was present and none of the seats used it — so treat unanimity as weak evidence, not strong.
4. **Ask for the falsifier.** "What measurement would show this answer is wrong?" A seat that cannot name one is speculating.

## 5. Reading the Answer

- **Unanimous** → weak-to-moderate evidence. Check first whether they were all given the same wrong framing.
- **Split** → the useful case. Report the split and the reasoning on each side; do not average them.
- **One seat contradicts a stated fact** → that seat is wrong, and say so plainly. In calibration, one seat proposed a hypothesis that could not explain the data it was given and then rationalised the contradiction rather than revising. This is a known failure mode of the whole class; look for it.

**The council never decides.** It is evidence for the user's decision. Never present a council answer as an approval.

## 6. Cost

Two of three seats are subscription-funded. DeepSeek is cash. Report the seats used and, if usage was captured, the cost; **if it was not captured, say so rather than estimating** — a dollar figure from an unverified price looks measured and is not.

Reply to the user in the language the user used.
