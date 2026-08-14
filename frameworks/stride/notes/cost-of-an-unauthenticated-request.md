# What One Unauthenticated Request Costs

## When this applies

A process is reachable from an untrusted zone, and what an anonymous request makes it do is unstated.

## What to look for

- **Asymmetry.** The question is what a cheap request makes the system spend: a search that scans, a report that aggregates, an export that serialises, a password hash that is deliberately slow, an image or document that gets parsed, a webhook that fans out. A threat here names the specific expensive path, not "the service can be flooded".
- **What is exhausted first.** Connection pools, worker threads, file descriptors, memory per request, third-party API quota and database connections usually run out long before bandwidth or CPU. The scarce resource is what makes the finding concrete.
- **What else shares it.** A saturated pool or a saturated database takes down every caller of the same dependency, including paths an attacker never touched. That blast radius is the impact.
- **Work done before authentication.** Parsing, decompression, schema validation, database lookups for the account and expensive key derivation often run before the request is rejected, which means the rejection itself is the expensive part.
- **Amplification and retries.** Client retries, queue redelivery and fan-out to downstream services multiply one request into many. A system that retries aggressively can flood itself.

## Guardrails

- Analysis knowledge, not evidence. Whether this system rate-limits, sheds load or caps request size is what the submitted material says.
- Avoid the generic finding. "An attacker can send many requests" is true of every networked system and tells a reader nothing; name the path, the resource, and what stops working.
- Availability loss is this lane. If the same overload makes the system fail *open* — skipping a check under pressure — the consequence belongs in whichever lane that check protects.
