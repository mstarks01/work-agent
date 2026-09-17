# 38. A component reaches the graph only in a zone

- **Status**: accepted
- **Date**: 2026-09-17
- **Effort**: the placement finding of
  [#1003's execution audit](https://github.com/mstarks01/work-agent/issues/1003),
  which asks that "the long-term modeling contract should allow known existence
  with unknown placement".
- **Relates to**:
  [ADR 0032](0032-a-derived-crossing-names-an-inferred-zone.md), which settles
  how a zone nobody stated is recorded, and
  [ADR 0034](0034-an-assertion-is-a-scoped-fact-with-a-support-span.md), whose
  catalog records what the sources say about a placement.
- **Evidence**: `run.py oracle` over the five signed cases. A perfect reading
  reaches 41 of 41 required rows, needs 18 inferred placements, and leaves no
  signed row the schema cannot carry.

## Context

`ZonedElement.trust_zone` is a plain string, so the **schema** already accepts
a component whose zone is the unknown sentinel. Two readers refuse it.
:func:`~analysis_service.validation.validate` reports `invalid-reference`,
because the field must name a **Trust Boundary** the model holds; and
`factbundle._place` drops the component before the gate sees it, reporting
`unplaced`, so the resolver never builds a model the gate would refuse.

The audit read that chain as a loss: a component the sources name, and whose
network location they do not, disappears along with its interactions and every
fact about it. That reading was right about the mechanism and the measurement
has since moved.

Three changes closed the measurable part. `_place` stopped reading the unknown
sentinel as the name of a zone; `_referent` stopped reading it that way too, so
a signed row recording "the sources do not place this" is expressible in a
bundle; and the matcher stopped charging a produced row the reference is silent
about as a wrong claim. What is left is the contract itself, and this decision
states it rather than changing it.

## Decision

**A component reaches the graph only in a zone. Where no source states one, the
zone is an inference the model records, never an absence the graph carries.**

Four rules, and the first three are already how the tree behaves.

**1. An unstated zone is inferred, and the inference is marked.** ADR 0032 is
the convention: the model places the component, records an
:class:`~analysis_service.system_model.Assumption` on `trust_zone`, and every
derived crossing names the endpoint whose zone was assumed. A reader who needs
to know whether a placement was stated reads the assumption, not the zone.

**2. Where no zone can be inferred, the component does not enter the graph, and
the loss is named.** A bundle that offers several zones and no way to choose
leaves the resolver nothing to infer from. Choosing between zones to obtain a
binding is the failure the experiment exists to avoid, so the component is
reported `unplaced` and every fact about it is `unresolved` under
`unplaced-subject` in the sidecar. It is a question a reader can answer, not a
row that vanished.

**3. What the sources state about a placement lives in the catalog, not in the
graph.** `network-membership` with the unknown sentinel and a reason is a fact
the assertion layer carries, and it is how a reference records that the sources
are silent. The graph says where the component was put; the catalog says what
the sources said about where it is. The two answer different questions and a
disagreement between them is information rather than a defect.

**4. The contract changes when a measurement asks it to, and not before.** A
`trust_zone` that admits an absence is read by 32 sites across nine modules,
and every framework rule that reasons about crossings would gain a case. That
is a large change to make on a mechanism, so it waits for a loss that shows up
in a number.

## What the measurement says today

`run.py oracle` puts a perfect source-backed reading through the real resolver,
gate and scorer over the five signed cases:

| | |
|---|---|
| required rows the path keeps | **41 of 41** |
| signed rows the schema cannot carry | **0** |
| wrong claims caused by the convention | **0** |
| inferred placements needed | 18 |
| blessed elements rebuilt | 48 of 51 |
| blessed flows rebuilt | 31 of 33 |

The three elements and two flows missing are one case's, and they fall because
the oracle could cite no span for `boundary:public-internet` — a limit of the
instrument rather than of the contract. No required fact is lost to rule 2 on
this corpus.

So the contract costs 18 marked inferences and nothing measurable. That is the
reason this decision states the rule rather than changing it.

## When to revisit

Any of these reopens it, and each is a number rather than an argument:

- a case where rule 2 drops a component that carries a required fact, so the
  oracle's ceiling falls below the corpus total;
- a framework rule that needs to reason about a component whose zone is
  genuinely unknown, rather than assumed;
- a corpus whose sources routinely name components with no inferable zone,
  which would show as a rising `unplaced` count in the sidecar.

## Consequences

- The sidecar is the record of rule 2, so anything counting what a route lost
  reads `unplaced` and `unplaced-subject` there rather than inferring it from a
  missing element.
- A route that follows rule 1 produces a placement row the reference is silent
  about. That row is `unruled` and never a wrong claim, which is the matcher's
  rule and not a special case for placement.
- `validate`'s `invalid-reference` on a sentinel zone stays as it is. It is the
  gate that makes rule 1 the only way in, and weakening it without rule 4's
  measurement would let a route skip the assumption.
