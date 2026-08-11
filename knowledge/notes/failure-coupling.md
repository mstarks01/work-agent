# Shared Dependencies and Coupled Failure

## When this applies

Two or more elements depend on a single element — a queue, a database, an identity provider, a cache, a configuration service — and what happens to the dependants when it degrades is unstated.

## What to look for

- **Who fails with it.** Enumerate the dependants and ask, for each, whether it degrades, queues, retries or stops. A dependency whose loss stops an unrelated user-facing path is worth more than one whose loss slows a batch job.
- **Degradation versus outage.** Slow is often worse than down: callers hold connections, thread pools fill, timeouts stack across hops, and the failure propagates to systems that do not use the dependency at all. Ask what the timeout is and what happens when it fires.
- **Retry behaviour.** Uncoordinated retries turn a brief stall into a sustained overload, and a retry storm frequently outlives the original fault. Backoff, jitter, circuit breakers and load shedding are the controls; their absence is the question.
- **Fail-open and fail-closed.** When an identity provider, a policy service or a feature-flag service is unreachable, something decides what the caller does. That decision is a security property, not only an availability one.
- **The single-writer chokepoint.** One queue consumer, one leader, one migration job or one scheduler is a place where a small failure becomes a total one, and it is usually drawn as an ordinary box.

## Guardrails

- Analysis knowledge, not evidence. Which dependants exist is a fact in the model; how this deployment handles the failure is what the submitter said, or `unknown`.
- The finding is a consequence, not a topology observation. "Several services use the queue" is a fact about the diagram; "an attacker who saturates the queue stops order processing and payment reconciliation with it" is a threat.
- Availability is this lane. A dependency failure that causes a *check* to be skipped is somebody else's finding.
