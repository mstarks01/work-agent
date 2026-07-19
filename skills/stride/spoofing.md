# Spoofing

## Scope

Spoofing is the S in STRIDE: an attacker successfully claims an identity that is not theirs — a user, an external system, a service endpoint — and the system acts on that false identity. The security property violated is **authenticity**. Your lane covers every way identity verification can be defeated: absent authentication, weak or replayable credentials, stolen or phished credentials, impersonation of a service to its callers, and trust granted by network position instead of verified identity.

Lane boundaries with the other five categories:

- Modifying data or code is **tampering** — but using a forged identity to get into position to modify is yours.
- Reading credentials out of a store or off the wire is **information disclosure**; presenting those credentials as one's own is yours.
- An authenticated actor denying what they did is **repudiation**, even when the dispute is about who acted.
- Overwhelming an authentication service is **denial of service**.
- Gaining more privilege than an identity legitimately has is **elevation of privilege**; being accepted as a different identity in the first place is yours.

## Applicability

Your element view is mechanically pre-filtered to External Entities and Processes. Data Flows are not analysis targets here, but their attributes (`authentication`, `protocol`, `encryption_in_transit`) are your primary evidence about the endpoints they touch — always read the flows attached to each element you analyze.

- **External Entity, `kind: human`** — ask how the system verifies this person. Examine every flow the entity initiates: what does `authentication` say, and does the mechanism resist phishing, credential stuffing, and session theft? An entity carrying the `credentials` asset tag is itself a spoofing target.
- **External Entity, `kind: external-system`** — machine identity. Look for static API keys, shared secrets, unsigned callbacks/webhooks, and absence of mutual authentication on flows in either direction. Also consider the reverse: can an attacker impersonate *your* system to this external party?
- **Process** — two directions. (1) The process is impersonated: callers reach a fake because flows to it lack server authentication (`encryption_in_transit: none` or `unknown`, no certificate validation implied by `protocol`). (2) The process is fooled: it accepts inbound flows whose `authentication` is none, weak, or `unknown`, or it trusts requests merely because they originate inside its `trust_zone`. `exposure: internet-facing` widens the attacker population; `internal` does not remove it.

## Threat Patterns

Each pattern names its trigger in the System Model attribute vocabulary. `unknown` means unverified — treat the control as absent for enumeration and flag the gap, but never assert it is absent.

- **Unauthenticated crossing** — trigger: an inbound flow with `authentication: none` or `unknown` that appears in the derived boundary crossings. Anyone in the source zone can originate requests as anyone. The single highest-signal spoofing trigger.
- **Bearer-only static credential** — trigger: `authentication` mentions an API key, basic auth, or long-lived token. Possession equals identity: theft, leakage in logs/URLs, or replay grants full impersonation with no expiry or binding to the caller.
- **Missing server authenticity** — trigger: `encryption_in_transit: none` or `unknown`, or `protocol` is plaintext (HTTP, unencrypted AMQP/JDBC). Clients cannot verify which endpoint they reached; an on-path attacker or DNS/ARP manipulation substitutes a look-alike process that harvests whatever callers send.
- **Phishable human factor** — trigger: `kind: human` and flow `authentication` limited to passwords or OTP codes. Credential stuffing from breach corpora and real-time phishing proxies defeat single-factor and code-based MFA; impact scales with the assets reachable by that entity's flows.
- **Unverified callback** — trigger: a flow initiated by an `external-system` (webhook, push) whose `authentication` is none or `unknown`. Anyone who learns the endpoint URL can inject fabricated events that downstream logic treats as authenticated facts.
- **Trust by network position** — trigger: a Process with flows whose `authentication` is none/`unknown` where both endpoints share a `trust_zone`. Identity is being inferred from reachability; any foothold in the zone (SSRF, a compromised neighbor) inherits every implicit trust relationship.
- **Cross-tenant identity confusion** — trigger: a boundary of `kind: tenant`, with flows crossing it whose `authentication` does not carry a tenant-scoped identity. One tenant's valid credential replayed in another tenant's scope is spoofing even though the credential itself is genuine.
- **Shared machine identity** — trigger: multiple flows from distinct sources showing the same `authentication` mechanism described as shared or common, or a `secrets`-tagged element feeding many consumers. One leak lets an attacker impersonate every holder, and audit cannot distinguish them.

## Guardrails

- **Second-order reach.** A spoofed low-value identity is a foothold, not an endpoint. After each threat, walk the model's flows outward: what can the impersonated identity reach, initiate, or read? Score impact on the full reachable set — impersonating a metrics agent that can write to a config store is a config-tampering enabler, and worth saying so in the description.
- **Attacker perspective.** State each threat as an attacker action with a named target: *who* is impersonated, *to which* element, *via which* flow ID. "No MFA on the login flow" is an observation, not a threat; "an attacker replays stuffed credentials over `flow:user-to-web:login` to act as any registered user" is.
- **Unknowns are findings, not assumptions.** When the trigger is an `unknown` attribute, write the threat conditionally, cite the attribute, and let the critic mark it needs-info. Never write "no authentication exists" when the model says `unknown`.
- **Stay in the model.** Reference only element IDs present in the System Model. Do not invent components, flows, or controls the extraction did not capture; if something material seems missing, note it against the nearest real element.

## Mitigations

Tie each mitigation to the pattern it addresses, and prefer mitigations expressible as changes to the model's own attributes.

- *Unauthenticated crossing / trust by network position*: authenticate every flow regardless of zone (zero-trust posture) — mTLS for service-to-service, OIDC/OAuth 2.1 for user-facing flows; make the flow's `authentication` explicit and verifiable.
- *Bearer-only static credential*: replace with short-lived, audience-bound tokens (OIDC ID/access tokens, signed JWTs with `aud` and expiry) or mTLS; rotate and scope any key that must remain static; never carry credentials in URLs.
- *Missing server authenticity*: TLS 1.2+ with full certificate-chain validation on every flow; pin or constrain issuance (private CA, SPIFFE) for internal service identity.
- *Phishable human factor*: phishing-resistant MFA (WebAuthn/passkeys) for entities whose flows reach `credentials`, `financial`, or `secrets` assets; breached-password screening; rate-limit and monitor authentication endpoints.
- *Unverified callback*: HMAC-sign webhook payloads with per-consumer secrets, include timestamp and nonce, and reject stale or replayed deliveries.
- *Cross-tenant identity confusion*: carry tenant identity inside the verified credential (claim-scoped tokens) and enforce it at the receiving process, never from request parameters.
- *Shared machine identity*: one identity per workload (per-service certificates or workload identity federation) so compromise is containable and audit lines are attributable.
