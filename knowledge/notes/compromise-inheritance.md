# What a Compromised Component Inherits

## When this applies

An internet-facing process originates flows across a trust boundary, and what its own identity is entitled to do on the other side is unstated.

## What to look for

- **Start from the assumption.** The interesting question is not whether the exposed process can be compromised but what the attacker holds once it is: its credentials, its network reach, its standing authority, and every downstream call it is trusted to make.
- **Standing credentials over scoped ones.** A long-lived key, a mounted service-account token or a database user with broad grants is inherited whole. Short-lived, narrowly scoped credentials limit the inheritance, and whether they are in use is worth asking rather than assuming.
- **Reach beyond the drawn flows.** A compromised process can call anything its network position and credentials permit, not only the flows the diagram shows it using. Egress restrictions and per-destination credentials are what make the drawn flows the real ones.
- **Authority wider than the job.** A component that needs to read one table and holds write on every partition carries the excess as latent attacker capability. The gap between the job and the grant is the finding.
- **The pivot that matters.** Follow the inherited authority to the asset: the store it can read, the queue it can publish to, the deploy pipeline it can trigger, the identity it can mint. That chain is the threat; the compromise itself is only its first step.

## Guardrails

- Analysis knowledge, not evidence. What this deployment grants its exposed components is what the submitter said, or `unknown`.
- Do not invent the initial compromise. The finding is what the inherited authority permits, and the exposure the model states is what makes it reachable.
- Gaining authority beyond a component's own is this lane. What the attacker then reads or alters may also be a finding, and it belongs to the agent whose lane it lands in.
