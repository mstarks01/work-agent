# 25. `unsure` is a mark, now that an unset one is refused

- **Status**: accepted
- **Date**: 2026-09-06
- **Amends**: the ruling in
  [#488](https://github.com/mstarks01/work-agent/pull/488), *A mark is agree,
  reject or duplicate*, which considered `unsure` and refused it. That ruling
  was right under its own premises. This record states which premise stopped
  holding, and why the answer changes with it.
- **Relates to**: [ADR 24](0024-a-case-sitting-merges-as-one-file.md), the shape
  of the record a mark rides in.

## Context

#488 gave two reasons to refuse `unsure` as a **Case Sitting** mark.

**The first.** `evals/harness/ledger.py` already spells `unsure` for a **Ledger**
voter who cannot decide, and a vote and a sitting mark are keyed by one
fingerprint. Two meanings on one word, over one key, is a trap.

**The second.** "The sitting already says *I cannot tell* by leaving the mark
unset."

The second premise stopped holding on 2026-09-06. A submission used to clear its
case on the digests alone, so a reader could open every set and record nothing
about any **Claim** — and CI counted the case as read. `check_every_finding_marked`
closed that: a sitting now records only when every recorded finding carries a
mark.

That fix removed the very mechanism #488 relied on. A reader who cannot judge
one entry had, from that moment, three answers available and none of them true.
The safe pick under a forced choice is `agree`, and `agree` is the mark that
inflates the agreement these numbers publish.

The first reason inverts on inspection. `ledger.py` glosses its own `unsure` as
"a real answer and counted as one", and records what earned it: review sitting
01 answered `unclear` on 4 of 30 pairs, and that finding fixed the specificity
rule in `BLESSING.md`. A voter's `unsure` and a reader's `unsure` are the same
judgement — they read the finding and cannot decide. One word for one meaning
across a shared key is the property to want, not the trap to avoid.

## Decision

**`unsure` is the fourth mark.** `Mark` is
`Literal["agree", "reject", "duplicate", "unsure"]`, and `MARKS` derives from
it, so both surfaces, the by-hand gloss and every check offer the same set with
no second list to keep level.

It is last in the set, because it is the answer a reader reaches for when the
other three do not fit, and a control lists it where they look for it.

**It moves no number, exactly as a vote's `unsure` moves none.** Nothing scores
a sitting mark today — a mark is evidence a person reads. The rule stands for
whatever reads them first: an `unsure` is a spent answer that counts toward the
sitting being complete and toward nothing else.

**It is not `needs-evidence`.** The **Ledger** keeps that word for a reviewer
who cannot answer from what they were shown, and it holds only for the sitting
it was cast in. A sitting reader was shown the whole case by construction, so
the distinction has nothing to bite on here.

## Consequences

**Answering every finding costs a reader nothing they do not believe.** That is
the point. The completeness rule and this one are one design: a bar that forces
an answer needs an answer for "I cannot decide", or it manufactures the
agreement it measures.

**The twelve generated reading documents change.** `MARK_GUIDANCE` names the
closed set, so the documents name it too. #488 could not regenerate them,
because a corpus edit then made the whole pull request a sitting. ADR 24 removed
that: a sitting is one file under `evals/review/submissions/`, and a corpus path
is an ordinary change again.

**One mark in the corpus keeps its meaning.** The filled sitting in case 01
recorded `agree` 46 times and no value moves.
