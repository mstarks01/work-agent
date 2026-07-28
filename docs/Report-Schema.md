# Report Schema

A successful analysis returns a `StrideReport` (from `stride_service.report`).
It is **self-contained**: every element a threat references resolves inside the
embedded system model, so a consumer needs nothing but the one payload. The
model validators enforce that on construction — a report that does not hold
together cannot be built.

Get one from either [the engine](Integration-Guide.md) (`outcome.report`) or the
[`/v1/jobs/{id}/report`](HTTP-API.md) endpoint.

## A rendered example

[`example-report.html`](example-report.html) is a self-contained viewer holding a
sample report — open it in a browser to see the payload below rendered as threat
cards, a severity summary, and the extracted DFD. It is a single file with the
report JSON embedded, so it also serves as a reference for laying one out.

## Top-level shape

```python
class StrideReport:
    schema_version: str          # "1.1"
    disclaimer: str              # AI-generated, not human-reviewed
    job: Job                     # id, status="completed", timestamps, revise_rounds
    input: InputRef              # system_name + source_sha256 of the exact text
    nodes: list[NodeRun]         # per-node model, sampling fingerprint, duration_ms
    sampling: dict[str, dict]    # per-tier resolved decoding params (provenance)
    system_model: SystemModel    # the canonical model the analysis ran on
    boundary_crossings: list[BoundaryCrossing]
    threats: list[Threat]        # confirmed + needs-info, severity-ordered
    rejected_threats: list[Threat]
    summary: Summary
```

`threats` holds the actionable findings; `rejected_threats` is the audit trail
of drafts the critic ruled out. Both are the same `Threat` type — placement is
decided by the verdict.

## A threat

```python
class Threat:
    id: str                          # "<letter>-<NN>", e.g. "S-01" (see letters below)
    category: StrideCategory         # spoofing | tampering | repudiation | ...
    title: str
    description: str
    affected_element_ids: list[str]  # element IDs in system_model — always resolve
    severity: Severity
    mitigations: list[Mitigation]    # {summary, detail}
    confidence: Rating               # low | medium | high (critic-calibrated)
    verdict: Verdict
```

Category letters: `S` spoofing, `T` tampering, `R` repudiation,
`I` information-disclosure, `D` denial-of-service, `E` elevation-of-privilege.
A threat's `id` must carry its category letter.

### Severity is derived, never asserted

`Severity` carries a qualitative `likelihood` and `impact` (`low|medium|high`);
`level` (`low|medium|high|critical`) is computed from a fixed matrix, so a model
cannot inflate it. `justification` explains the two inputs.

```python
from stride_service import derive_severity_level

derive_severity_level("high", "high")     # "critical"
derive_severity_level("medium", "low")    # "low"
```

### Verdict

The critic's ruling on the threat:

```python
class Verdict:
    status: "confirmed" | "needs-info" | "rejected"
    reason: str                          # required unless confirmed
    related_unknowns: list[UnknownRef]   # required iff needs-info
```

- `confirmed` — grounded in the model's facts.
- `needs-info` — plausible but hinges on an `unknown` attribute; each such
  attribute is named in `related_unknowns` (`{element_id, attribute}`). Stays in
  `threats`.
- `rejected` — not grounded. Moves to `rejected_threats`.

## Summary

Counts a UI can render without walking the threat list:

```python
class Summary:
    threat_count: int
    by_category: dict[StrideCategory, int]
    by_severity: dict[SeverityLevel, int]
    needs_info_count: int
    rejected_count: int
    elements_analyzed: int
```

`summary` is computed from the report's own contents and must match them, so it
is safe to trust without recounting.

## Provenance

The report records exactly which models and decoding parameters produced it, so
a result stands on its own without trusting any outside record.

```python
class NodeRun:
    node: str                        # graph node name, e.g. "extract", "critic"
    model: str | None                # the SERVED build, vendor-prefixed; None for code-only nodes
    requested_model: str | None      # the CONFIGURED route this node asked for
    sampling_fingerprint: str | None # 64-hex identity hash of (served route, decoding params)
    duration_ms: int
```

- **`sampling`** lists the decoding parameters each tier actually used, once per
  tier: `{"base": {"temperature": 0.0, ...}, "strong": {...}}`. A parameter left
  to the model's default shows as `null`.
- **`model`** is what *answered* — the build the provider reported, joined to its
  vendor prefix, e.g. `vertex_ai/gemini-2.5-pro-002`.
- **`requested_model`** is what was *asked for* — the configured route, e.g.
  `vertex_ai/gemini-2.5-pro`.
- **`sampling_fingerprint`** is a hash of the served route plus those parameters
  — one value identifying exactly how a node generated its output. It can be
  recomputed from the node's `model` and its tier's entry in `sampling`, so
  anyone can verify it. Code-only nodes (like `assemble`) have all three as
  `null`.

> **`schema_version` 1.1** added `requested_model` and redefined `model` as the
> served build rather than the configured string. A consumer keying on `model`
> now reads what answered.

The report records both model fields and **compares neither**. Their disagreement
is the drift signal, and it needs no comparison logic: a moved build produces a
fingerprint no approved baseline blessed, so drift falls out of certification.

The fingerprint is computed **per node execution**, so a node that ran more than
once — the critic on a revise path — appears once per execution, and a build that
moved mid-run gives one node two identities. That is the signal, not a defect.

The fingerprint — not `seed` — is what makes a result reproducible to reason
about; `seed` is best-effort, and some vendors do not accept it at all. The
report carries the fingerprints as-is. Whether a run matches a baseline this
*deployment* approved is a separate question, answered against
`config/blessed-fingerprints.toml` and recorded on the job, never on the report:
the report is portable evidence, a manifest is one deployment's claim. See
[Configuration → Provenance and certification](Configuration.md#provenance-and-certification).

A report produced without live models (the in-memory stub runner, or eval
fixtures) simply has an empty `sampling` and no fingerprints.

## Serialising

`StrideReport` is a Pydantic model. For JSON (dates as ISO strings, the shape a
front end consumes):

```python
report.model_dump_json()          # str
report.model_dump(mode="json")    # dict
```

## The other outcome

An input that cannot be modelled yields no report — the engine returns a
`PipelineRejected` carrying `list[ValidationIssue]` instead (see
[Integration-Guide](Integration-Guide.md)). Each issue has a `code`, a human `message`, and
optionally the `element_id` / `field` it concerns.
