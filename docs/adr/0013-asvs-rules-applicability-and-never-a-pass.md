# 13. ASVS rules applicability and never a pass, and no run of it is compliance

- **Status**: accepted
- **Date**: 2026-08-14
- **Effort**: [#176 — ASVS 5.0 as the second framework
  package](https://github.com/mstarks01/work-agent/issues/176), on the map
  [#158](https://github.com/mstarks01/work-agent/issues/158) settled
- **Amended by**: [#659](https://github.com/mstarks01/work-agent/issues/659), which
  splits the critic's first check into two rejection causes; see the note under
  the decision.
- **Relates to**: [ADR 0010](0010-package-cannot-extend-the-evidence-catalog.md)
  and [ADR 0011](0011-package-text-follows-its-retrieval-key.md), the two
  rulings the framework contract already carries about what a package may and
  may not do. This one is written by addition and amends neither.

## Context

ASVS 5.0.0 publishes 345 requirements across 17 chapters. The standard says what
verification of one costs:

> Aside from penetration testing (using valid credentials to get full
> application coverage), verifying ASVS requirements may require access to
> documentation, source code, configuration, and the people involved in the
> development process.
>
> — `0x04-Assessment_and_Certification.md`, tag `v5.0.0`

A job here carries prose. It carries no source code, no configuration and no
running application, and its interviewee is a transcript rather than a person to
question. So one half of the pass/fail decision the standard defines is not
reachable from the input this service accepts.

Two counts make the gap concrete rather than theoretical. **31 of the 345
requirements verify that a document exists** — documented input validation rules,
documented anti-automation controls, documented authorization rules — and every
one of them sits in the first section of its chapter, which the standard states
as its own rule. No representation of a running system can hold that answer,
because the answer is an artifact of the organization that built the system.
Separately, **33 of the 70 level 1 requirements read one security control's
configuration parameter** — a password minimum length, a TLS version, a cipher
mode — and a **System Model** carries a free-text attribute where the requirement
needs a number.

The second question arrives with the first. ASVS ranks its requirements into
three levels and tells the organization to pick one, so a job here names a level
and the run rules on that level's requirements alone.

## Decision

**An ASVS claim rules whether a requirement applies and whether the input settles
it. It never reports a pass.** The three neutral **Verdict** states carry the
question this service can answer:

- `confirmed` — the requirement applies to this system, and the input does not
  show it satisfied.
- `needs-info` — the requirement applies, and the input does not settle it.
- `rejected` — the critic rules that the requirement does not apply.

No fourth state is added to reach for a pass, and no field carries one. A draft
arguing that a requirement is met is rejected, and the reason given is that the
input cannot carry that answer.

> **Amended by [#659](https://github.com/mstarks01/work-agent/issues/659).** A
> `rejected` verdict rules that the requirement does not apply only when its
> `rejected_because` is `evidence`. The audit found that the critic's first
> check had one outcome for two different failures: the model's facts settle
> that the requirement has no subject here, and the draft's own argument does
> not hold while the requirement still applies. The second now answers
> `reasoning`, rules on nothing, and leaves the requirement on the scope list
> as `not-raised`, beside `lane` and `duplicate`. A rejection for an unsupported
> gap no longer reads as an exclusion.

**No output of this service is a compliance result, and a level-filtered run is a
fork of ASVS rather than ASVS.** The word compliance appears in no verdict
rationale, in no lane skill and in no claim description; the package's own
`disclaimer.md` states both limits, and every **Framework Analysis** carries it.

## Consequences

**The record grades nothing.** No severity, no confidence and no mitigations —
the requirement text is the remedy, and repeating it as a recommendation would
turn a ruling into advice. Two things follow mechanically: the package carries no
`severity_rubric.md`, and the gate's existing rule against a rubric beside a
non-grading record fires for the first time on something real rather than on a
test fixture.

**A refusal is an answer.** A requirement the framework rules out appears in the
block as a rejected claim or as a **Scope Entry**, never as an absence. The
standard asks for exactly this — "some requirements may be non-applicable, and
this must be noted in the report" — and it is what lets a reader tell a
requirement that does not apply from one nobody looked at.

**Every requirement in the selected level appears exactly once**, as a claim or
as a scope entry, and the block's own checks hold that true. At level 1 that is
70 entries per job; at level 3 it is 345.

**The block records its level.** A reader holding one block can tell which
requirement set produced the answer, which is what stops two runs of one system
at two levels from being read as one result.

**This is not a step toward certification.** Nothing here is a promise that a
future release will report a pass, because the limit is the input's rather than
the implementation's. A service that accepted source code and configuration
would be answering a different question, and it would be a different effort.
