# Identity at a Trust Boundary

## When this applies

A flow crosses from one trust zone into another and its `authentication` is `unknown`, absent, or names something that identifies a channel rather than a caller.

## What to look for

- **Who can originate the flow.** The population in the source zone is the attacker population. A zone named for the public internet means anyone; a zone named for an internal network means anyone who has already reached it, which is a smaller set but rarely a controlled one.
- **What the destination does with the identity it is handed.** An identity the caller supplies — a user ID in a header, a tenant in a path segment, an account number in a body — is a claim, not an assertion, unless something verifies it. The classic failure is a receiver that trusts the field because the sender is "internal".
- **Network position presented as authentication.** "Only reachable from the VPC", "behind the load balancer", "internal only" describe reachability. They answer *who can connect*, never *who is calling*, so they do not survive one compromised neighbour or one misrouted request.
- **Credential versus identity.** A shared API key, a static bearer token or one client certificate baked into an image authenticates the *fleet*, not the member. Anything holding the credential is the same principal, which matters here and matters more for attribution.
- **What the impersonation buys.** A threat is the action taken as somebody else, not the missing control. Follow the flow to what the destination does on receipt — the write it performs, the record it returns, the downstream call it makes on the caller's behalf.

## Guardrails

- Analysis knowledge, not evidence. Nothing here is a fact about the system under review; ground a finding in what the submitter wrote, an `unknown` attribute, or a derived crossing.
- `unknown` is not `none`. An unstated control may exist and go undescribed — write the threat conditionally so the critic can rule it needs-info — while a stated absence is a fact you may rely on.
- Being accepted as another identity is spoofing. Holding an identity legitimately and reaching past what it is entitled to is elevation of privilege.
