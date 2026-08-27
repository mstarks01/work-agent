# 4. Category agents select evidence; the service constructs grounds

- **Status**: accepted
- **Date**: 2026-08-08
- **Effort**: [#126 — replace LLM-constructed Ground objects with deterministic
  evidence references](https://github.com/mstarks01/work-agent/issues/126)
- **Relates to**: [ADR 0002](0002-finding-level-attribution.md), whose flat
  `Ground` this keeps and whose stated cost it removes.

## Context

A `Ground` has three kinds and each requires different fields. That relationship
is not expressible in a JSON schema a provider will reliably compile — `oneOf`
has the thinnest and least uniform support across the vendors a category agent
may be routed to, and an uncompilable grammar costs a dead run rather than a
degraded one. ADR 0002 chose the portable flat object and named the price: the
schema does not prevent a mis-shaped `Ground`, so pydantic catches one on
arrival, at a seam with no re-ask path.

That price came due. A run emitted a ground declaring itself `derived-fact`
while carrying an `attribute` and an empty `flow_id`. It failed the node's
`output_schema` validation, the workflow cancelled the five sibling lanes, and
the job died — an agent that had identified a real fact lost six lanes of
analysis to the field it spelled it into.

Sampling does not fix this. `temperature = 0` narrows variation; it does not
make a language model a schema processor, and with six independently-routed
agents per run a small per-agent probability compounds. The relationship
`kind → required fields` was being carried entirely by prompt instruction, which
makes a mis-shape an expected outcome rather than a defect.

## Decision

**An agent names the facts it relied on. The service builds the records.**

After validation, `evidence_catalog()` enumerates every mechanically derivable
fact in the System Model — each attribute holding the `unknown` sentinel, and
each derived boundary crossing — against the canonical `Ground` for it, under a
stable ID: `unknown:<element-id>:<attribute>` and `crossing:<flow-id>`. Agents
are shown the IDs.

A category agent emits a `ThreatProposal`, which replaces `grounds` with two
flat lists a schema compiler can express exactly: `evidence_refs`, IDs copied
from the catalog, and `quotes`, a span plus the source it came from.
`resolve_proposals()` turns each reference back into the entry it names and
assembles a quote's ground from the two fields, producing the `DraftThreat` the
rest of the graph already worked in.

**The report is unchanged.** `Ground` keeps its shape, its three kinds and its
flat encoding; `schema_version` does not move. What changed is who writes one.

## Consequences

**The mis-shape is unreachable rather than rare.** No model-facing schema
contains a `Ground`, so there is no field through which a branch and its
contents can disagree. The validator on `Ground` remains, now checking this
service's own construction — a tripwire whose expected value is zero, and whose
non-zero reading is a code defect rather than a rate to tune.

**A new failure mode replaces it, and it is narrower.** An agent can name an ID
that is not in the catalog. That raises `EvidenceResolutionError`, a
`DraftJoinError` — the same class as a draft citing an element the model does
not contain, discovered at the same seam, with the same consequence. It is
counted separately by the eval sweep (`unresolved-evidence`), because the number
worth watching after this change is exactly how often an agent composes an ID
instead of copying one.

> **Amended by [ADR 0009](0009-a-bad-reference-costs-its-entry.md).** That number
> was watched, and it was 2 of 12 jobs. A single unresolvable reference is now
> dropped and marked rather than fatal; only a threat left with no grounds at
> all still raises `EvidenceResolutionError`. The closed set and the resolution
> seam below are unchanged.
>
> **Amended again by [ADR 0017](0017-a-groundless-claim-costs-its-entry.md).**
> The groundless threat is now dropped and marked too, and
> `EvidenceResolutionError` no longer exists.

**No repair, and no fuzzy matching.** An unresolvable reference is reported as
itself. Inferring which fact an agent *meant* — reading a `derived-fact`
carrying an `attribute` as an intended `unknown-attribute`, say — is mechanical
but is exactly the guess this decision removes; the ambiguity is prevented
rather than resolved.

**The catalog cannot express a conclusion.** Every entry is derived by one of
two rules from the validated model, so "authentication is unknown on this flow"
is catalogable and "authentication is weak on this flow" has no way in. Whether
a catalogued fact participates in a credible attack stays the agent's judgement
and the critic's to rule on.

**Quotes stay outside the catalog**, and could not be in it: choosing a span for
what it states is the judgement no enumeration can make. An agent proposes one;
the pinned ladder in `stride_service.grounding` still checks it against the
source it names at the fan-in, and an unverifiable quote is still marked rather
than dropped.

**Every prompt exemplar now shows selection rather than construction**, so the
exemplar system carries its own evidence catalog, and CI checks that every ID an
exemplar cites is in it. An exemplar composing an ID would teach the one habit
that fails a whole lane.
