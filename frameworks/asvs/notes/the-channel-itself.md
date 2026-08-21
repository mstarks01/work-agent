# The Channel Itself

## When this applies

A flow states a protocol or a transport protection. Chapter V12 covers the connection: what protects it, which version and suite are permitted, and whose certificate gets verified.

## What to look for

- **Read the flow, not the name.** This chapter's subject is the wire. A process called a TLS terminator says nothing about what the flows behind it carry, and that segment is usually the interesting one.
- **The stated version is the start of the question.** A protection named for one hop leaves open whether older versions are still accepted on the same endpoint, which suites are permitted, and whether the connection can be downgraded.
- **Verification is the half that gets skipped.** Encrypting a channel without validating the peer's certificate leaves it open to anyone who can answer for the name. Where the description names mutual TLS, both directions carry the requirement.
- **Internal is not protected.** A private network, a cluster, a VPC or a service mesh is a location, not a control. A flow inside one with no stated protection is an applicable requirement and an unsettled one.
- **Defaults are per protocol.** Message brokers, database wire protocols, log shippers and metrics agents commonly default to plaintext, and a description naming one without stating protection is a fair question.
- **Outbound counts.** Connections the application makes to a third party carry these requirements exactly as inbound ones do.

## Guardrails

- Analysis knowledge, not evidence. Cite the flow's `protocol` or `encryption_in_transit` value, or the submitter's own words.
- Rule applicability, never a pass. "HTTPS everywhere" does not confirm the version, the suites or the verification.
- The primitive itself — algorithm, mode, key length — is V11. What the channel does with it is here.
