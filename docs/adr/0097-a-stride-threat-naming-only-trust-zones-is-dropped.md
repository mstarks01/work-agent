# 97. A STRIDE threat that names only trust zones is dropped

- **Status**: accepted
- **Date**: 2026-10-17
- **Related**: [ADR 0031](0031-a-claim-identity-carries-no-direction.md),
  which keeps the claim identity as it is
- **Evidence**: `tests/test_report.py`
  (`test_a_threat_naming_only_trust_zones_is_refused`,
  `test_a_lane_batch_drops_a_proposal_naming_only_trust_zones`),
  `tests/test_fan_in.py`
  (`test_a_claim_left_naming_only_trust_zones_is_dropped_and_marked`),
  and the offline replay in this ADR's pull request

## Context

A STRIDE claim's identity reads its targets: `endpoint_targets` turns each
cited flow into its two endpoints and drops each **Trust Boundary**, because a
zone is the context a claim sits in and not what it is about. `finding_key`
in the service and the eval fingerprint both read that set.

A claim that cites only `boundary:` IDs therefore has an empty target set.
`affected_element_ids` had `min_length=1`, and the fan-in reference check
passed such a claim. So two claims of one lane and one verb at two different
zones had one key. `report_changes` matched them as one finding, and one vote
answered for both.

A claim gets to zones alone on two routes. A lane can propose it so, or the
fan-in can drop the other references beside a zone: a reference that the
model does not contain, or one that the grounds do not reach.

## Decision

**A STRIDE threat that names only trust zones is dropped**, by the
maintainer's ruling 2026-10-17. The identity rule does not change.

- `is_zone` in `system_model.py` is the one reader of "is this ID a zone".
  `endpoint_targets` and the eval readers call it.
- `Claim.untargeted` says why a list of element IDs gives a claim nothing to
  be about. The neutral answer refuses nothing. `DraftThreat` refuses a list
  of zones alone.
- The validators on `DraftThreat` and `ThreatProposal` call it. A lane batch
  validates each proposal alone, so a proposal of zones alone is a
  **Dropped Claim** that names the rule, and the batch stands.
- The fan-in calls it after the passes that narrow `affected_element_ids`,
  and drops each claim that it refuses as a **Dropped Claim** with the same
  reason.
- There is no re-ask. The lane seam has no repair pass, and a drop is the
  path that an empty `affected_element_ids` takes.
- `frameworks/stride/output.md` tells the lane that a zone is context and
  that a threat naming only zones is dropped.

ASVS does not change. An ASVS claim names a catalog requirement, and
`finding_key` keys it by that requirement and does not read its targets. An
empty target set is the ordinary case for ASVS: a requirement about a coding
practice names no element.

## Consequences

- No STRIDE claim that reaches the critic has an empty target set, so two
  claims at two zones are never one finding by their targets.
- A STRIDE threat that names only zones costs that one claim and never the
  job. The report lists it with its reason.
- A report that holds such a threat no longer loads. No archived report,
  emission or corpus file holds one today.
- The live rate of such drops is unmeasured.
