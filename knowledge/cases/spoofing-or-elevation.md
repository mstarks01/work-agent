# The Same Flow, Two Lanes

## Pattern

An authenticated support user calls an administrative endpoint that accepts a `tenant_id` parameter. The caller's identity is verified. Nothing in the model says what binds the parameter to the tenants that user supports.

## Considered

Both: impersonating another tenant's administrator, and reaching another tenant's data with one's own identity.

## Ruling

Accepted as one elevation-of-privilege finding, filed in that lane and not in spoofing.

## Why

The distinction is not about the consequence but about what the attacker does with identity. Spoofing is *being accepted as someone else*: the receiver believes the request came from a principal it did not. Elevation is *doing more than your own principal may*: the receiver knows exactly who is calling and lets them act beyond it.

Here the identity is genuine and verified. What fails is the authorization decision on the parameter, so the finding is elevation. Had the endpoint accepted a caller-supplied user identifier as proof of who was calling, the same request would have been spoofing.

Filing it in both lanes costs the critic a dedupe pass and gets one copy rejected. Where a chain genuinely contains both steps — forge an identity, then use it to exceed authority — the two findings are separable, and each names its own step rather than the whole chain.

## What decided it

The model stating a verified caller identity on the flow, together with the absence of any stated check on the tenant parameter.
