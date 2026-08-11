# Transport Protection and On-Path Positions

## When this applies

A flow's `encryption_in_transit` is `unknown` or absent, and the flow either crosses a trust boundary or touches an element holding a graded asset.

## What to look for

- **Where the plaintext actually is.** TLS terminating at a load balancer, a service mesh sidecar or an API gateway protects the hop in front of it and nothing behind it. The interesting segment is usually the one the diagram draws as a single line.
- **Who occupies the path.** In a cloud or cluster network the on-path population is not "someone with a tap": it is any workload that can be scheduled on the same node, any compromised sidecar, any service that can be induced to proxy, and anything with the ability to change routing or DNS.
- **Read versus alter.** The same missing protection supports two different findings. Reading the channel is information disclosure and turns on what the payload contains; altering it is tampering and turns on what acts on the altered data downstream.
- **What the payload carries.** Credentials, session tokens and internal identifiers in transit are worth more than the business data beside them, because they extend the attack rather than end it.
- **Protocol defaults.** Message queues, database wire protocols, log shippers and metrics agents frequently default to plaintext inside a private network, and a model that names one without stating transport protection is a fair question rather than an accusation.

## Guardrails

- Analysis knowledge, not evidence. Ground the finding in the model's own `unknown` or in what the submitter wrote about the channel.
- "Internal network" is not transport protection; say so as a question about what protects the segment, not as a claim that nothing does.
- Keep the lanes apart: what an on-path attacker *reads* is information disclosure, what they *change* is tampering.
