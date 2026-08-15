# Attribution, Shared Credentials and the Record

## When this applies

An unverified caller acts on an element holding a graded asset, or a flow's stated credential is one a population shares, and what the record would say about the actor is unstated.

## What to look for

- **What the log names.** Service accounts, connection-pool users, machine identities and shared API keys all produce records naming the *conduit* rather than the actor. The question is whether the originating principal survives the hop — usually it must be propagated deliberately, and usually the model does not say that it is.
- **Whether the record would survive a dispute.** Repudiation is about the argument afterwards, not the logging in general. Ask who could plausibly deny an action, what evidence contradicts them, and who is able to alter or delete that evidence.
- **Who can write the log.** A log the acting component can rewrite, or that sits in the store the same caller can write to, is not independent evidence of anything. Retention shorter than the time to notice has the same effect.
- **Clock and ordering.** Correlating an action to an actor needs consistent time and a stable identifier across systems. Free-text messages without a request or trace identifier rarely support attribution when it is needed.
- **The action worth attributing.** Not every action needs to be attributable. Money moving, permissions changing, data exports, configuration changes and anything a regulator or a customer would later ask about are where the absence bites.

## Guardrails

- Analysis knowledge, not evidence. Whether this system logs an action is what the submitted material says, not what this note assumes.
- Repudiation findings need a *disputable* action and a named gap in the record. "Logging is not described" is an observation; "the operator can deny issuing the refund because the record names only the shared service account" is a finding.
- Do not file the underlying identity weakness here as well. That is the spoofing lane's; this lane is about what the record can prove.
