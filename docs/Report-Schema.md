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
    schema_version: str          # "1.0"
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

The report records the exact generation identity it ran on, so a result defends
itself without trusting any external record.

```python
class NodeRun:
    node: str                        # graph node name, e.g. "extract", "critic"
    model: str | None                # served model; None for deterministic FunctionNodes
    sampling_fingerprint: str | None # 64-hex sha256(served model, resolved tier sampling)
    duration_ms: int
```

- `sampling` is the per-tier **clear block**: tier name → the resolved decoding
  params that tier's nodes used (`{"flash": {"temperature": 0.0, ...}, "pro":
  {...}}`), recorded once per tier as plain scalars. An unset param is `null` —
  the model applied its own default.
- Each LLM node's `sampling_fingerprint` is
  `sha256(served model, resolved tier sampling)`, binding model and sampling into
  **one** hash keyed on the *served* model, and is **recomputable** from that
  node's `model` plus its tier's entry in `sampling`. A deterministic
  `FunctionNode` (e.g. `assemble`) carries neither a model nor a fingerprint.

What makes a result defensible is the fingerprint, not the `seed` — `seed` is
best-effort and guarantees nothing. The report carries **raw** fingerprints
only; whether a run is *certified* (its fingerprints match a blessed baseline) is
computed in the eval/CI layer against `evals/blessed-fingerprints.toml`, never
asserted on the report itself. See
[Configuration → The eval gate](Configuration.md#the-eval-gate-and-provenance).

Both `sampling` and `sampling_fingerprint` are empty/absent on a report with no
LLM provenance — a stub-runner or eval-synthetic report.

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
