# The STRIDE profile this service implements

STRIDE is a threat-identification method, not a standard with a conformance
test. It names six categories and a way of walking a data-flow diagram; it does
not say how a tool should record a finding, how many findings one attack is, or
how severe anything is. Every such decision is this repository's own.

This document is the list of those decisions. It exists because the report's
disclaimer says "STRIDE threat model", and a reader who knows the published
method will otherwise assume choices nobody made on their behalf. `STRIDE_VERSION`
in `src/analysis_service/frameworks/stride/record.py` names the version of what
is written here, and moves when any of it changes.

## What is the published method

The six categories and the properties they violate are Microsoft's, and this
package follows them:

| Category | Property violated |
|---|---|
| `spoofing` | authentication |
| `tampering` | integrity |
| `repudiation` | non-repudiation |
| `information-disclosure` | confidentiality |
| `denial-of-service` | availability |
| `elevation-of-privilege` | authorization |

So is the per-element approach: the five element types this service models —
external entity, process, data store, data flow, trust boundary — are the
published taxonomy, and each lane walks the elements its category can apply to.

## What this repository decided

Each of these is a choice with a reason. None is required by STRIDE, and a
different tool may reasonably choose otherwise.

**A closed set of 20 action verbs.** A finding names one attacker action from
`analysis_service.actions.ACTION_VERBS`, grouped into families. STRIDE names
categories, not actions. The vocabulary exists so two spellings of one attack
compare equal without a model call — it is half of what
`evals/harness/fingerprint.py` keys a finding on — and it is what lets a human
vote be reused across runs. The cost is that a finding whose action the set
cannot spell has to be filed under the nearest verb.

**One category per finding.** A finding is filed in exactly one lane, and a
legal-verb table decides which lanes each verb may be filed in. STRIDE does not
forbid a threat from being two categories at once. A draft filed under a verb
its lane does not take is **rejected**, never recategorised, so a category error
costs the finding rather than moving it. That keeps the categorisation
measurable — it is observable only because misfiled drafts are visible — and it
means the report undercounts a threat that genuinely spans two categories.

**One finding per action and place.** Two drafts naming one verb against one set
of endpoint-resolved elements are one finding. This is coarser than reality: two
interpreter injections at one process may need different controls and count as
one here, and two principals escalating at one target likewise. Where a fix
would differ, the drafts are kept — the critic's duplicate test is one
countermeasure closing both, not one harm.

**A local severity matrix.** `frameworks/stride/severity_rubric.md` maps a
likelihood and an impact, each a model judgement, onto a band by arithmetic this
repository defines. STRIDE defines no severity scheme at all. The band is a
qualitative ordering and never a probability, and the two ratings behind it are
kept so a reader can disagree with the arithmetic rather than only with the
result.

**Flat trust zones.** Every entity, process and store belongs to exactly one
trust zone, and a boundary crossing is a flow whose two ends carry different
zone IDs. Network position, organisational ownership and privilege level are
three things in reality and one field here. A shared service enforcing per-tenant
authority crosses no drawn zone, so an authorisation threat inside one zone has
no structural crossing to rest on and must be argued from the text.

**Flow direction is who initiates.** A data flow records the initiator, not a
one-directional movement of bytes. A service reading a store and a service
writing it are modelled the same way, and the response to a request is implicit.
Rules therefore raise leads about connections rather than about operations.

**Repudiation extends to audit stores.** The traditional per-element table does
not apply repudiation to a data store. This package does, because a log a
subject can rewrite is the repudiation finding a reader most needs. That is an
extension of the method, not a correction of it.

## What none of this measures

The report says an attacker action against the named elements is credible
against what the submitted text stated. It does not say a control is absent, it
does not say anything unlisted is safe, and no number in it is a probability.
`frameworks/stride/disclaimer.md` carries that sentence to every reader; this
document says what shaped the finding underneath it.

## Sources

- [Microsoft's STRIDE category descriptions](https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats)
- [Microsoft's per-element methodology](https://learn.microsoft.com/en-us/archive/msdn-magazine/2006/november/uncover-security-design-flaws-using-the-stride-approach)

Both support the comparison above and none of the measurements elsewhere in this
repository.
