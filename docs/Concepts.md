# Concepts

This page explains the terms used by Work Agent. For implementation details,
see [Architecture](Architecture.md).

## Source

A **source** is text submitted for analysis. It can be a system description or
a transcript. Every source has a label, and source order does not give one more
authority than another.

The current engine accepts text. Files such as DOCX, PDF, VTT, or source-code
archives must be converted or analyzed elsewhere before submission.

## System model

The **system model** is the structured data-flow model extracted from the
sources. It contains five kinds of elements:

- external entities, such as users and third-party systems;
- processes, meaning running components that transform data;
- data stores;
- directed data flows; and
- trust boundaries.

Every selected framework analyzes this same validated model. Boundary crossings
are calculated in code from the trust zones of a flow's endpoints; the
extraction model does not supply them.

## Unknown and assumption

`unknown` means the sources did not establish a security-relevant attribute. It
does not mean the control is absent.

An **assumption** is an inference made during extraction. The inferred value is
stored on the element and a separate assumption record identifies the element,
attribute, and basis. A value left `unknown` is not an assumption.

## Framework and framework package

A **framework** defines the security question being asked. STRIDE asks about
credible threats. The current ASVS package asks which ASVS 5.0.0 requirements
apply and what the submitted prose cannot settle.

A **framework package** is the implementation of that question: its lanes,
rules, output record, prompts, local reference material, and reviewer guidance.
The repository currently registers `stride` and `asvs` in
`analysis_service.frameworks.PACKAGES`.

## Precondition

A **precondition** decides whether a framework applies to the validated system
before its analyzers run.

STRIDE accepts every valid data-flow model. ASVS looks for a process presenting
a web interface or a flow using a web protocol. If the model establishes that
the system is not web-based, ASVS is marked not applicable. If the model does
not say, ASVS is skipped with a reason explaining that more input is needed.
The other selected frameworks can still complete.

## Specialized analyzer (lane)

A **lane** is one specialized part of a framework. STRIDE has six category
lanes. ASVS has 17 chapter lanes. Each lane gets its own model call and proposes
claims only for its assigned area.

Lanes within a framework run in parallel. A framework is not silently shortened
when one lane produces no output: the job fails because an absent lane and a
lane that deliberately returned an empty list mean different things.

## Candidate

A **candidate** is a lead that code derives from the system model. For example,
a STRIDE rule can notice that a flow crosses a trust boundary while its
authentication is unknown.

A candidate is not a finding, not evidence, and not copied into the report as a
claim. It only directs the lane analyzer's attention. The analyzer must decide
whether a security claim follows and must ground that claim separately.

## Ground

A **ground** is the evidence a claim rests on. Code, not a model, constructs the
final ground objects from references selected by a lane analyzer.

Grounds can represent:

- a quote from a labeled source;
- an attribute the source left unknown;
- an attribute the source explicitly said was absent;
- a trust-boundary crossing derived from the model; or
- the absence of a named element from the model.

Every carried claim has at least one ground. Quotes are checked against the
source text. If a close source span can be found, code repairs the quote and
records the change. If none of a claim's grounds can be verified, code drops
that claim and records why.

## Proposal, claim, and verdict

A lane model emits a **proposal**. Code validates it, resolves its evidence,
creates its stable ID, and turns it into a draft **claim**.

A claim receives one **verdict**:

- `confirmed` — the claim holds under that framework's meaning;
- `needs-info` — the claim is relevant but depends on information the input did
  not establish; or
- `rejected` — the draft failed the framework review and remains as an audit
  trail rather than an actionable claim.

The words are framework-specific. A confirmed STRIDE claim is a credible
threat. A confirmed ASVS claim means the requirement applies and the prose does
not show it satisfied. It does not mean a compliance check failed.

Claims grounded on unknown attributes are assigned `needs-info` in code before
the reviewer model sees them.

## Framework reviewer (critic)

The **critic** is the reviewer model for one framework. It sees that framework's
drafts together and judges what code cannot decide from structure alone. It can
reject unsupported or misfiled drafts, identify duplicates, and calibrate the
fields that framework owns, such as STRIDE severity.

Code checks the review output. Every draft must receive exactly one coherent
ruling, and a ruling cannot name a draft that does not exist. A malformed review
gets one model retry. If the retry is still malformed, the job fails instead of
returning a partial report.

## Mechanical check

A **mechanical check** is code with a definite answer. Examples include schema
validation, ID and reference checks, boundary-crossing derivation, quote
matching, claim-ID construction, summary recounting, and verification that the
report contains exactly the framework blocks requested.

Mechanical checks improve consistency, but they do not make the security
analysis deterministic. Extraction, claim generation, and review still use
probabilistic models.

## Provenance

**Provenance** is the report's record of how it was produced. The report stores:

- a digest of the submitted sources;
- a digest of the built model instructions;
- the configured and provider-returned model identifiers for each model call;
- resolved sampling settings and a per-call sampling fingerprint;
- node timing and token use when available; and
- which deterministic rules and local reference documents informed each
  framework.

These fields support comparison and investigation. They do not prove that the
system model or findings are correct.

## Fingerprint and certification

A **sampling fingerprint** hashes the provider-qualified model identifier that
answered a call together with the resolved sampling settings for its tier. It
does not include the input, prompt text, or output. Those are represented
elsewhere—or, for the full output, by the report itself.

**Certification** compares the fingerprints observed in a completed report
with a deployment-local allow-list in `config/blessed-fingerprints.toml`. It can
show that the model-and-sampling setup was one the operator approved. It does
not certify finding accuracy, guarantee repeatability, or certify the complete
prompt and input combination.

## Outcomes

An analysis has three terminal outcomes:

- **Completed:** a full report is available.
- **Rejected:** extraction and its one repair attempt could not produce a valid
  system model. Structured issues tell the submitter what failed.
- **Failed:** caller input, a provider, a deadline, or an internal check failed.
  No partial report is returned.

A completed multi-framework report includes one block per selected framework,
even when a framework's precondition skips its analyzers. The block then records
why it did not run.
