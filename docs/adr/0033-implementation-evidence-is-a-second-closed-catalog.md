# 33. Implementation evidence is a second closed catalog, built from a pinned snapshot

- **Status**: proposed. Phase 1 starts only after a merged ASVS Baseline exists
  to compare against, and it ships measured or not at all.
- **Date**: 2026-09-12
- **Effort**: [#484 — research GitHub-backed code and configuration evidence
  for ASVS analysis](https://github.com/mstarks01/work-agent/issues/484), whose
  research note is `docs/research/repository-evidence.md`, frozen at `8085c43`,
  and whose review comment of 2026-09-08 lists the ten corrections this ADR
  adopts.
- **Relates to**:
  [ADR 0008](0008-retrieval-by-fired-rule.md), whose rejection of lexical and
  semantic search this ADR keeps for advisory text and bounds for evidence;
  [ADR 0010](0010-package-cannot-extend-the-evidence-catalog.md), whose three
  tests the second catalog restates over a tree digest;
  [ADR 0012](0012-the-catalog-carries-a-stated-absence.md), the first widening
  of the catalog argued against those tests;
  [ADR 0013](0013-asvs-rules-applicability-and-never-a-pass.md), which stands
  unchanged: no field carries a pass.

## Context

An ASVS **Claim** today rests on prose. A **Lane Agent** may say a requirement
needs `code` or `config`, and the service withholds that claim as a **Scope
Entry** in state `needs-other-evidence`, because
`analysis_service.sources.CARRIED_EVIDENCE_KINDS` names prose alone. The
constant is threaded as an argument to every reader, and its own docstring
says it becomes a job property the day a job carries something else.

The research note measured the gap on the corpus: 61 of 96 ASVS reference
records expect `needs-code` or `needs-config`, and every one is a deferral
today. The figure comes from a corpus a person has read one case of, and the
labels moved in the same window, so it orders the work and justifies no
architecture on its own.

The note compared five options. Option A, raw retrieval by lane agents, fails
on cost, reproducibility and prompt injection. Option D, scanner ingestion
alone, covers a small part of the standard. Option B builds a closed catalog of
implementation facts from a pinned commit by deterministic extractors, and a
lane selects from it the way it selects an **Evidence Reference** today. Option
C adds bounded raw excerpts the service copies by location. The review of the
note accepted B with C's excerpts in a later phase, and asked for ten
corrections before any of it becomes a decision. This ADR is that decision.

## Decision

**Implementation evidence is a second closed catalog, built by the service from
an immutable artifact snapshot, that a lane selects from and never extends.**
The **System Model** stays the architectural representation of the system, and
no repository fact overwrites it. Ten rules bound the shape.

1. **The core contract is provider-neutral.** A job carries a repository
   locator beside its **Sources**, never among them. An injected provider
   adapter resolves the locator to an artifact snapshot: an immutable commit
   and tree identity, fetched under size bounds. GitHub is the first adapter
   and not the architecture. A credential lives in the connector layer and
   never enters a job record.

2. **Evidence availability is per requirement, never job-wide.** A repository's
   presence does not make the carried kinds `("prose", "code", "config")` for
   the whole job. Presence says nothing about whether the repository holds
   relevant code, relevant configuration, or the configuration in force. The
   unit is one requirement: it raises an evidence request, retrieval answers
   with an outcome, and a bundle carries what was found.

3. **An unsuccessful search is not a claim.** A claim needs a **Ground**. When
   retrieval finds nothing relevant, the requirement stays a Scope Entry in
   state `needs-other-evidence`, and the report carries a retrieval coverage
   row that says the search ran and what it covered. Absence of retrieved
   evidence is never evidence of absence.

4. **Candidate rules may rank retrieval and may not gate it.** ADR 0008 keys
   the retrieval of advisory text on a rule fired from the System Model. That
   restriction would let an incomplete prose model hide contradicting code.
   The primary selector is a requirement evidence profile the framework owns,
   keyed by lane and checked against the lane roster. A model candidate may
   narrow or rank the result and may not remove the only retrieval path.

5. **The model cites locations and never creates them.** The deterministic
   retriever selects locations and materialises bounded excerpts before the
   lane runs. The lane cites an existing evidence ID. It proposes no path and
   no line range, so every provenance field is checkable by code.

6. **The evidence names are artifact-based.** Two ground kinds: an
   `artifact-fact` with a path, a line range, an extractor identity and a
   content digest, and an `artifact-excerpt` the service copies by location.
   The names admit an uploaded archive, a local tree, an SBOM or a generated
   configuration without a second schema change.

7. **Committed configuration and effective configuration are two facts.** Each
   evidence record carries an authority scope: `repository`, `deployment`,
   `runtime` or `organization`. The four coarse routing kinds stay, and the
   scope says which state a fact describes.

8. **A scanner result binds to the snapshot by more than one route.** SARIF
   2.1.0 is the interchange. A run binds through its version control
   provenance, or through trusted provider metadata about the run. A run bound
   by neither is refused. A message or a snippet inside a run is never
   rendered to a model.

9. **Scanner licensing is policy and capability configuration.** A scanner is
   an optional adapter that a deployment enables or not. No architectural rule
   names a scanner as forbidden; the licence files decide, and they are read
   at the time a deployment enables the adapter.

10. **Raw and rendered evidence stay apart.** Secret redaction never mutates the
    canonical snapshot and never moves an offset. A record carries the original
    content digest, the rendered digest and the redaction metadata. Parsers and
    scanners run in an isolated, read-only, network-disabled worker under
    resource limits, and nothing in the snapshot executes.

**The requirement outcome semantics, which ADR 0013 fixes:**

| Retrieval result | Report treatment |
| --- | --- |
| no repository, or an unsupported artifact | Scope Entry `needs-other-evidence` |
| retrieval ran and nothing relevant was found | Scope Entry `needs-other-evidence`, plus a retrieval coverage row |
| evidence shows a gap | a grounded `confirmed` claim |
| evidence is relevant and insufficient | a grounded `needs-info` claim that names what remains |
| evidence appears to support the control | no pass; a non-verdict coverage row records what was seen at this commit |

A `supported-by-implementation` Scope Entry state is the earliest positive
shape, and it waits for a repository-backed corpus that a person has read, so
its false-support rate can be measured. It is not decided here.

**The System Model is not enriched.** A repository fact that contradicts the
prose is a finding, and an overwrite would delete it. Explicit enrichment is
stage 3 of `docs/research/system-model-evolution.md` and needs its own ADR.

**Against ADR 0010's three tests.** The catalog is a pure function of the
artifact snapshot and the extractor set, so the same tree digest yields the
same catalog. It is framework-neutral: every lane of every framework reads the
same record shapes, and a framework owns only its evidence profiles. It
introduces new IDs, which ADR 0012's widening did not, and that is why the
catalog is a second one with its own owner rather than a growth of the first.

## Consequences

**The sequence, in order, and none of it is started by this ADR.**

1. This record.
2. Provider-neutral snapshot and evidence types, provenance, limits and the
   retrieval coverage schema, with no change to any finding.
3. One GitHub adapter and deterministic configuration extractors, against a
   small ASVS fixture set a person has read.
4. The targeted second review pass for requirements that asked for code or
   configuration.
5. Bounded excerpts, then optional SARIF and scanner adapters.
6. A positive support state, argued against ADR 0013 after measurement.

**Phase 1 waits on a merged ASVS Baseline.** The gain this work promises is a
fall in deferral and in false prose requests, and both are unmeasured on the
current code. The first repository sweep needs that number to compare against.
If the phase 1 sweep does not move deferral or false prose requests on the
lanes it covers, the work stops at phase 1 and the measurement is recorded.

**What falsifies the decision.** A human read of the corpus that reclassifies
the `needs-code` and `needs-config` records as prose at scale. A measured
phase 1 in which lanes cite excerpts that are present and irrelevant at a rate
the **Critic** misses. A paired case in which a repository setting cannot be
overridden by any deployment, at a rate that makes *seen at this commit* and
*in force* one fact for a class of requirements.

**What was considered and rejected.** A job-wide carried kinds tuple, because
a repository's presence does not settle what it holds. Retrieval gated on a
fired candidate rule, because the prose model is the thing the code may
contradict. Repository-prefixed evidence names, because the first non-GitHub
artifact would force a rename. A `verified` verdict, because a repository shows
what was committed and never what runs. Any execution of repository code,
which rules out builds and generated scanner databases.

## Framework parity

ASVS is the framework whose claims route on an evidence kind, so it is the
first consumer. STRIDE: nothing changes, because a framework whose claims
compose an identity from an action and a place declares no evidence kind and
withholds nothing. A package added later reads the same record shapes and
declares its own evidence profiles, or declares none and reads no catalog.
