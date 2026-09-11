# 29. A vote binds to the content it read

- **Status**: accepted, built in [#743](https://github.com/mstarks01/work-agent/issues/743)
- **Date**: 2026-09-09
- **Effort**: [#743 — bind substantive and writing review to a content version](https://github.com/mstarks01/work-agent/issues/743),
  finding 11 of the audit on [#732](https://github.com/mstarks01/work-agent/issues/732)
- **Builds on**: `docs/agents/claim-identity.md`, which this keeps whole

## Context

A **Claim**'s identity is the framework, the lane, the endpoint-resolved
**Element** IDs and the action verb or catalog identifier, keyed by
`evals.harness.fingerprint.key_claim`. A vote stores those components and its
fingerprint, and every reader of the ledger looks a produced claim up by that
key: the scorer's standing, the review queue, the sitting page and the writing
instrument.

The identity ignores the title, the description, the grounds, the verdict, the
severity and the mitigations. That is what makes it an identity: the same
finding spelled two ways is one finding. It is also why a vote outlives a
rewrite of everything it judged. The audit replaced every retained case 01
title and description with text asserting that no attacker action exists, and
the scorer still matched 12 references. A person who voted a finding down for
`poorly-written` keeps that vote against a version whose prose is new, and a
person who voted one up for substance keeps that vote against a version whose
grounds are gone.

## Decision

**A vote records a digest of the content it read, and a reader applies the
vote only to content with that digest.** Two keys on every vote row:

| key | what it names | who reads it |
|---|---|---|
| `fingerprint` | the topic: one finding at one place in one lane | the scorer's match and standing, the queue, the sitting, `rekey` |
| `content` | the argument: the verdict, the grounds and the ratings the reader answered | the standing's *validity* |
| `prose` | the words: the description and the mitigations the reader answered | the writing instrument |

`content` is a sha256 over the claim's **structural** content: the verdict's
status, the catalogued grounds as (kind, place or term, attribute) with quotes
left out, and the package's own ratings where the record grades harm. Prose is
left out on purpose, and the measurement below is why: a digest that read the
description and the mitigations carried across two runs of one configuration
0 times in 234 pairs, because a model writes new prose every run, so a vote on
prose would go stale on every sweep and the ledger would never carry a
standing forward. The structural digest carried 155 of 234, and it is what a
substance vote judges: which fact the claim rests on, and what the critic made
of it. The set is read off the record's fields, so a package that grades
nothing digests less, and a package nobody has written yet digests what it
declares.

**A writing vote binds to the prose it read, and expires with it.** A style
objection is about the words on the page. It carries a second digest over
`description` and `mitigations`, and reads live only against those words,
which in practice means within the sitting that cast it. That is the honest
scope of a style vote, and the writing instrument counts stale ones apart.

**A standing is live only while the content is the one voted on.** When the
produced claim's digest differs from the vote's, the vote stands in the ledger
and the standing reads `stale`: a fifth standing beside `rejected`, `pooled`,
`open` and `unvoted`. A stale vote gates nothing and pools nothing, and the
queue serves the finding again with the earlier answer shown beside the new
content, so the reader re-reads a change rather than a paraphrase.

**The writing instrument reads only live votes.** A style objection is about
the words on the page; the same objection against new words is a guess.

**`rekey` is unchanged.** It recomputes fingerprints from components; the
content digest is a fact about a version and never moves with the identity
rule.

## As built

`evals/harness/content.py` computes both digests and `tests/test_evals_content.py`
holds them. Four things the record left open, settled in the build:

**The queue re-asks a named reviewer only.** `queue.restated` narrows to the
voter the queue was built for, and returns nothing for an unnamed queue. In a
queue built for nobody, re-offering a re-argued finding would tell this reviewer
that another reviewer had answered the earlier version — the leak that killed
the `unmatched` priority row. The reader's own earlier answer rides on
`QueueItem.previously` and is shown on the page.

**A re-argued finding ranks last.** A new priority row, `restated`, below `new`:
the topic already has an answer, which buys less than a finding nobody has
answered at all.

**The tables are read off the record, not off a framework name.**
`STRUCTURAL_FIELDS` and `PROSE_FIELDS` name shared judgement fields a package's
record may declare — `Verdict`, `Severity`, `Mitigation` — and a claim
contributes the entries its own record carries.
`TestTheTablesAnswerForEveryPackage` checks both against every package's ruled
record, in both directions, and finds those records off the blocks they fill so
a package added tomorrow is checked with no edit.

**The artifact version does not move.** Both new facts sit inside blocks
version 5 already declares — `stale` in an unlisted threat's counts, `carried`
in a writing row — nothing asks an older artifact for them, and a bump would
have made the one merged Baseline unreadable by the commands that exist to read
it.

## Consequences

**A standing goes stale when the argument moves, and only then.** A prompt edit
that rewrites descriptions leaves every substance vote standing, which is what
the measurement below bought. A change that moves a verdict, a ground or a
rating re-opens the votes on the findings it touched, and that is the honest
cost: the person judged a different argument. The stale count rides in the
artifact's standing counts, so a reader can see how much of a sweep's standing
rests on a re-read nobody has done.

**The ledger row grows two fields.** A row written before them carries an empty
digest, which reads as live against any content, the precedent
`rejected_because` set for a report read back: recording nothing is truthful.
No row carries one — the ledger directory does not exist yet, so this record
costs no migration and every row ever written will name what it judged.

**A style vote lives about as long as its sitting.** The prose digest cannot
tell a reworded description from a rewritten one, and a model writes new prose
every run, so a style objection rarely survives to the next sweep. The
alternative, a threshold on textual distance, is a second reader of "are these
the same words" beside the identity rule, and the identity's whole design is
that no model and no similarity measure decides a match. The writing instrument
counts what expired as `carried`, so the shortness is visible rather than
reading as prose nobody objected to.

## Alternatives considered

**Digest the whole claim, title included.** Rejected: the identity already
carries the title's meaning, and a retitle for readability would stale every
vote a sitting produced.

**Retire a stale vote.** Rejected: a stale vote is history a re-reader wants
beside the new content, and a retired vote hides what a person once said.

**Version the vote by the sweep that produced the finding.** Rejected: two
sweeps of one configuration produce one content twice, and a vote should
carry across them.

## Measurement

Run offline on 2026-09-09 over the six case 01 STRIDE runs of one
configuration (the pre-flight and the five spread runs of that day): 27
distinct fingerprints, 21 seen in two or more runs, 234 pairs of runs on one
fingerprint.

| digest | pairs that share it |
|---|---:|
| description, grounds, verdict, severity, mitigations | 0 of 234 |
| verdict status, catalogued grounds, ratings | 155 of 234 |
| verdict status alone | 222 of 234 |

The first row is the record as first proposed, and it fails: no vote would
carry between runs. The second row is the decision above. The third row
carries most but binds to nothing a substance vote reads. What remains to
measure is the same table over the thirteen-case Baseline once a second
sweep of its configuration exists, which waits on spend.
