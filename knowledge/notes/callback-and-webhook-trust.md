# Callbacks, Webhooks and Inbound Events

## When this applies

An external system or a third party originates a flow into this system — a callback, a webhook, a delivery receipt, a partner push — and its `authentication` is unverified.

## What to look for

- **The endpoint is public whether or not it is advertised.** Callback URLs leak through browser history, referrer headers, mobile binaries, error pages and support tickets. Treat "nobody knows the URL" as a delay, not a control.
- **What verifies the sender.** The three common answers are a shared secret in a header, a signature over the body with a timestamp, and mutual TLS. Each fails differently: a shared secret is replayable by anyone who reads one request, an unsigned timestamp is replayable forever, and a signature nobody checks is decoration.
- **What the receiver does before it trusts the payload.** Inbound events usually mutate state — mark an order paid, provision a tenant, close a ticket. The interesting question is what an attacker gets to assert by sending a well-formed event, not whether the parser is safe.
- **Replay and ordering.** Even a properly signed event can often be sent twice, or out of order, or after it has been superseded. Idempotency keys and event sequence numbers are what make that boring; their absence is worth asking about.
- **The reverse direction.** A system that accepts callbacks usually also makes outbound calls to a partner-supplied URL. That is a different threat (a request the system will make on an attacker's behalf) and belongs in whichever lane the consequence lands.

## Guardrails

- Analysis knowledge, not evidence. A protocol's common failure mode is not a claim about this deployment.
- Where the model does not say what verifies the sender, that is `unknown`: the threat is conditional and the critic may rule it needs-info.
- Forging the partner's identity is spoofing; altering an event in flight is tampering; flooding the endpoint is denial of service. One unverified callback can raise all three, filed in three lanes by three agents.
