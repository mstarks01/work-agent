# 6. Two exemplar systems, and "near" is either of them

- **Status**: accepted
- **Date**: 2026-08-09
- **Relates to**: [ADR 0004](0004-evidence-references.md), whose evidence catalog
  each worked system must now render, and [ADR 0002](0002-finding-level-attribution.md),
  whose quote ladder the exemplars are held to by the same code the agent is.

## Context

All eighteen exemplars in `prompts/exemplars/` were written against one
reference system: the payments platform defined in `analyze.md`. A customer, a
web API, a ledger service, an accounts database, an audit bucket, three network
zones — synchronous request/response throughout.

One system is load-bearing in three places, which is why this could not be a
quiet edit. `test_prompt_lints.py` resolves every exemplar's element IDs and
runs the shipped quote ladder over every exemplar quote against the one
labelled source block. `ANALYZE_PROMPT_TOKEN_CAP` is sized with the exemplar
block explicitly counted. And the corpus tags every case
`exemplar_proximity: near | far`, split 1/11, with `01-payments-checkout` alone
as the control.

The problem is what the exemplars teach beside the method. An agent shown
eighteen worked threats that are all one architecture has no way to separate
the reasoning from the domain it was demonstrated in — and the far-domain cases
are exactly where that failure would surface. The exemplars carry the method;
that they also carried a monoculture was incidental, and it was never argued
for.

Diversifying them is not free of instrument cost. `exemplar_delta` in
`evals/harness/scorer.py` subtracts far-domain recall from near-domain recall,
so changing which architectures the exemplars demonstrate changes what "near"
means for the whole corpus. The instrument had to be re-specified in the same
change or it would drift silently into measuring nothing.

What made that cheap is that **no sweep has ever produced a delta number**.
Confirmed rather than assumed: across all 58 `Evals (live Vertex)` runs the
`Run golden-case evals` step is skipped every time, both scheduled runs died
earlier still at credential setup, the API-key lane has never got past a
workflow-file error, and the repository holds zero run artifacts. There is no
measured baseline here to invalidate — only a designed instrument to re-point
before it is first read.

## Decision

**Two worked reference systems in `analyze.md`, and `near` means an
architecture the exemplars demonstrate.**

System B is a fleet telemetry platform: sensor gateways behind a shared client
certificate, a public MQTT broker, a stream processor that trusts a tenant ID
out of the device payload, and a shared time-series store. It is event-driven
and multi-tenant where A is synchronous request/response, and its trust
problems arrive as data rather than as calls. Six of the eighteen exemplars —
one per category — are rewritten against it.

B is deliberately smaller than A: four elements and three flows against six and
five. It exists to contrast, not to be a second full-fidelity model, and the
contrast is what the reader is meant to take from it.

**No exemplar mixes the two.** A threat is an argument about one system, so
each exemplar works one end to end. `owning_system` in the lints enforces it by
failing any draft whose cited elements no single system covers — resolving
against the union of the two would accept exactly the mixing the prompt tells
an agent never to do.

**Two is the argued number, not a step toward six.** #115 asks for something
closer to five or six architectural families. Cost here is linear in systems —
~500 tokens on all six agents' instructions, on every job — and the diversity
bought is not: the first contrasting system breaks a monoculture, the fifth
mostly restates it. `ANALYZE_PROMPT_TOKEN_CAP` moves 2950 → 3450 to pay for
one, and the cap stays sized so a third has to be argued for rather than added.

> **Amended by [ADR 0016](0016-the-token-caps-are-drift-alarms.md).** The
> constant named here is now the `prompts/analyze` entry of `TOKEN_CAPS`, and the
> 6-8K envelope it was argued against is retired. A cap states how far the
> text has drifted and rations nothing.

**"Near" stays a bit, not a scale.** It now means "an architecture one of the
worked systems is written in", which is a property that survives the exemplar
set growing. Each system gets one corpus control, and the pairing is
definitional rather than a judgement about resemblance: a control's domain is
the domain its exemplar system was written in. `01-payments-checkout` keeps the
role for A; `02-iot-fleet-telemetry` is re-tagged `near` for B — which is also
why B was written as fleet telemetry, since it makes the control a case the
corpus already had.

## Consequences

**The delta still asks its one question, and has lost a finer one.** Whether
recall depends on having been shown the architecture is answerable exactly as
before. Which *particular* exemplar system a gap belongs to is not — for that,
read the per-case recalls behind the aggregate.

**The far population is one case smaller**, 11 → 10, against 2 near. Every near
control is a case taken out of `far_recall`, which is where the honest question
is asked, so the populations are now guarded:
`test_the_near_controls_are_outnumbered_by_far_cases` asserts `near * 2 < far`.
At 2 of 12 that is comfortable, and the guard exists so a third exemplar system
cannot quietly make it not.
`test_exactly_one_near_exemplar_control` is replaced by
`test_one_near_exemplar_control_per_exemplar_system`, which pins the pairing.

**Every exemplar lint now asks which system a draft belongs to first.** IDs,
quotes and evidence references all resolve per-system, and both systems ship a
labelled source block and an evidence catalog for that reason — not an
editorial one. The quote check still runs the shipped ladder, imported rather
than reimplemented.

**The token raise lands on all six agents, every job.** That is the real price
and it is recurring; it is argued in the comment on the cap itself rather than
only here, because that is where someone about to raise it again will be
standing.

**What was considered and rejected: retiring near/far for per-domain recall.**
Honest once the exemplars span domains, and the right answer eventually — but
it trades a two-population comparison for a twelve-bucket one on a twelve-case
corpus, where most buckets would hold a single case. The instrument would get
more precise and much noisier in the same move, before a single sweep has read
it. Revisit when there are enough cases per domain to mean anything.

**Also rejected: five or six systems, as #115 reads.** The cost argument above
is the whole reason, and there is a carrier for what those systems would have
bought: the domain packs in `skills/domains/` from #122, which load per job and
cost nothing on jobs that do not earn them. Per-technology depth belongs there.
Exemplars carry the method, and two architectures are enough to show that the
method is what transfers.

**Also rejected: authoring a new near-control case for B.** The strongest
version of this — a purpose-built control per exemplar system — is also the
most SME work, and writing B into a domain the corpus already covered gets the
same control for none of it. The cost is that B's architecture was chosen partly
by what the corpus held; that constraint was acceptable once, and would be worth
re-examining rather than repeating if a third system is ever argued for.
