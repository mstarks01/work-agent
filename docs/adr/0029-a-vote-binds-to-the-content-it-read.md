# 29. A vote binds to the content it read

- **Status**: proposed, revised after the measurement below
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
| `content` | the version: what the reader was shown when they answered | the standing's *validity*, and the writing instrument |

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

## Consequences

**Some standings go stale on the next sweep.** Every prompt edit rewrites
descriptions, so a sweep after a prompt change re-opens the votes on the
findings it produced. That is the cost, and it is the honest one: the vote
was on text that no longer exists. The count of stale standings is a number
the artifact carries, so a reader can see how much of a sweep's standing rests
on a re-read nobody has done.

**The ledger row grows one field.** A row written before the field carries an
empty digest, which reads as live against any content, the precedent
`rejected_because` set for a report read back: recording nothing is truthful.
The first sitting after this record refreshes the rows it touches.

**A harmless paraphrase still goes stale.** The digest cannot tell a reworded
description from a rewritten one. The alternative, a threshold on textual
distance, is a second reader of "is this the same argument" beside the
identity rule, and the identity's whole design is that no model and no
similarity measure decides a match.

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
