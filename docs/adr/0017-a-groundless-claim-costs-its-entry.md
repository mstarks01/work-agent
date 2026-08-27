# 17. A groundless claim costs its entry, not the job

- **Status**: accepted
- **Date**: 2026-08-27
- **Amends**: [ADR 0009](0009-a-bad-reference-costs-its-entry.md), which kept
  one whole-job failure on the grounding path, and
  [ADR 0002](0002-finding-level-attribution.md), which set it.
- **Relates to**: [ADR 0004](0004-evidence-references.md).

## Context

ADR 0002 made an unverifiable quote a mark and kept one fatal case: a claim on
which *no* ground verifies. ADR 0009 did the same for evidence references and
kept the same fatal case. Both argued that a claim dropped for a reason recorded
in a list nobody must read is a silent removal, and that a dead job at least
tells someone.

Two things changed after that. `unknown_claim_identities` (schema 3.0) drops a
claim on the same terms — the claim is gone, and a mark with its title is the
trace — and the viewer renders that list as a block-level note. So the drop is
not silent any more. And ASVS arrived. Its lane agents write claims against a
catalog that rarely holds the fact a requirement turns on, so an ASVS claim
often carries one quote and nothing else. For such a claim, one misquote is a
total loss, and a total loss killed 17 lanes of work. A run with one model in
both tiers died on exactly that: claim `v5.0.0-5.3.1`, one quote, absent from
its source.

The fatal path also recorded nothing. The error named the claim and not the
quote, and nothing persists a draft, so the next failure was a guess.

## Decision

**A claim that loses every ground is dropped and marked.** Both producers write
the same mark, `GroundlessClaim`: evidence resolution, for a claim whose every
reference is outside the catalog, and the quote check at the fan-in, for a claim
whose every quote is absent from the source it names. The mark carries the
claim ID, the title, and a `reason` that repeats the lost quotes or the cited
references, bounded to 500 characters.

**A dropped claim gets no per-reference mark.** `unresolved_evidence` names a
claim the block carries, and the report refuses one that does not. The
groundless mark names the references instead.

**Nothing on the grounding path fails a job any more.** `GroundsUnverifiedError`
and `EvidenceResolutionError` no longer exist. The fan-in still fails closed on
a dangling element reference, a duplicate ID and an unresolvable source label,
which are shape errors and not citation errors.

**The eval sweep counts the drop.** `fail-closed` and `unresolved-evidence`
were failure kinds read off the exceptions. They are now a `groundless_rate`
read off the report's marks, denominated in drafts plus drops.

## Consequences

**The viewer lists the drop under the block**, beside the unpublished
identifiers, with the title and the reason. A reader sees which finding was
lost and why.

**Rejected: a lane re-ask.** A re-ask spends a paid model call to fix a
citation, and the fan-in has no re-ask path by design. The drop is visible and
costs nothing.

**Rejected: keeping the job failure for the quote case only.** A claim with no
grounds is one fact whichever producer found it, and two policies for one fact
is the branch that [framework parity](../agents/framework-parity.md) warns
against.
