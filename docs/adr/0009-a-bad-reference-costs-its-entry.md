# 9. A bad evidence reference costs its entry, not the job

- **Status**: accepted
- **Date**: 2026-08-11
- **Amends**: [ADR 0004](0004-evidence-references.md), whose closed-set design is
  unchanged and whose fail-closed consequence is narrowed here.
- **Relates to**: [ADR 0002](0002-finding-level-attribution.md), which established
  marking beside a threat rather than failing on it.
- **Amended by**: [ADR 0017](0017-a-groundless-claim-costs-its-entry.md), which
  drops and marks the groundless threat this ADR still failed the job on.

## Context

ADR 0004 gave agents a closed catalog and resolved their references against it,
reasoning that "an agent can only pick from the closed set it was shown or name
something that is not in it". The second case failed the job.

A live sweep measured what that costs (#138). **Two of twelve jobs died**, on 11
distinct bad references across 5 threats. Every one was well-formed — correct
`unknown:<element>:<attribute>` grammar, plausible element IDs, real attribute
names — and absent from the set. Agents had learned the ID *grammar* and were
composing references by pattern instead of copying from the list.

The prompt-side causes are addressed separately (#152: the catalog renders as a
table, the prompt stops teaching the grammar). This decision is about what the
service should do when an agent gets it wrong anyway, which no prompt change
makes impossible.

The tree already answered a version of this question twice, and both times in
the other direction:

- **`unverified_grounds`** — a quote the service cannot find in its named source
  is *marked per entry*, and the job fails only where no ground on a threat
  verifies at all.
- **`unresolved_mentions`** — an element ID in prose that names nothing is
  *marked, never fatal*, because "discarding six lanes of analysis over a
  mistyped ID in prose trades a whole report for a typo".

Whole-job failure on an evidence reference was the odd one out, and it was never
argued for on its own — it came along with the closed set.

## Decision

**Marked per reference, failed closed per threat.** An unresolvable reference is
dropped, recorded as an `UnresolvedEvidence` mark, and the threat stands on
whatever else it cited.

**The line is *no grounds at all*.** A threat whose evidence resolves to nothing
and which quoted nothing has no justification left. `grounds` is `min_length=1`,
so such a threat cannot be represented, and no critic could rule on it — that,
and only that, still raises `EvidenceResolutionError`.

**Dropped, not rendered.** Unlike an unverified quote there is nothing to show:
the catalog is the only source of a ground's branch and fields, so a reference
outside it yields no object. The mark is the entire trace, which is why it
carries the reference verbatim.

**No repair, unchanged from ADR 0004.** There is still no fuzzy match and no
nearest-entry guess. Inferring which fact an agent *meant* is the class of guess
the closed set exists to remove; the reference is reported as itself.

## Consequences

**Most of the jobs that died now return.** Grounds average 2.96 per threat, so a
threat with one composed reference beside real ones is the common shape of the
failure, and it now survives. How much of the 17% this recovers is not
predictable from one sample.

**A returned report no longer implies every citation resolved.** That guarantee
was an absence — the job either failed or it did not — and consumers relying on
it must now read `unresolved_evidence`. Schema 2.9 records the change; the list
is where the guarantee lives.

**The viewer shows it beside the threat**, worded as a citation failure rather
than as doubt about the finding: the grounds shown below the note are the ones
that resolved, and they are why the threat is still there.

**A silently dropped finding is still not available.** The one outcome nobody
gets is a threat disappearing without a trace — a groundless threat fails loudly
instead. That is deliberate: silently removing a finding is the worst thing a
security tool can do, and it stays the one branch that costs the run.

**What was considered and rejected: dropping the groundless threat and marking
it.** It would make the service never fail on citations at all, which is
superficially attractive. But a threat is the unit a reader acts on, and one
deleted for a reason recorded in a list nobody is required to read is exactly
the silent removal above. Failing tells someone.

**Also rejected: keeping fail-closed and relying on the prompt fix alone.** The
prompt changes in #152 are unmeasured, and even a large improvement leaves a
tail. The rate is not the argument anyway: one bad citation on one threat should
never have been able to discard five other lanes of correct analysis.
