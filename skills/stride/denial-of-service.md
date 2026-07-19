# Denial of Service

## Scope

Denial of service is the D in STRIDE: an attacker degrades or destroys the availability of a process, store, or flow for its legitimate users — by exhausting a finite resource, triggering crashes, poisoning shared state, or cutting a dependency the element cannot function without. The security property violated is **availability**. Your lane covers volumetric and asymmetric exhaustion (CPU, memory, storage, connections, quotas, budgets), amplification, crash-loop inputs, lockout abuse, and cascading unavailability through dependency chains.

Lane boundaries with the other five categories:

- Corrupting data where the *integrity loss* is the harm is **tampering**; corruption whose point is to crash or wedge a consumer is yours.
- Flooding an authentication service is yours; *bypassing* authentication is **spoofing**.
- Destroying audit logs to escape accountability is **repudiation**; filling a disk with garbage until logging (and everything else) stops is yours.
- Resource-exhaustion that leaks data through timing or error behavior is **information disclosure** — note it for that lane.
- Abusing an admin capability to shut services down got its power from **elevation of privilege**; the availability harm is yours to enumerate.

## Applicability

Your element view is mechanically pre-filtered to Processes, Data Stores, and Data Flows.

- **Process** — the primary target. `exposure: internet-facing` means the whole internet is the attacker population; rate limiting, input-size caps, and timeout posture are rarely stated, so expect `unknown`-shaped gaps. Ask what is *cheap for the attacker and expensive for the process*: unauthenticated endpoints that do real work (search, render, crypto, LLM calls) are asymmetric by construction. An `availability-critical` asset tag raises the stakes of any outage.
- **Data Store** — exhaustion of storage, connections, and throughput. Unbounded inserts fill disks and quotas; connection-pool starvation takes down every process sharing the pool; queues grow without bound when consumers stall. `technology` indicates the store's failure mode (hard quota vs. degradation vs. runaway cost).
- **Data Flow** — the flow as a chokepoint and a weapon. Flows with no `authentication` can be driven at line rate by anyone in the source zone; boundary-crossing flows from untrusted zones carry hostile volume inward. A flow can also be the *victim*: saturating one shared link or gateway starves every flow multiplexed through it.

## Threat Patterns

Each pattern names its trigger in the System Model attribute vocabulary. `unknown` means unverified — enumerate conditionally and flag the gap; never assert the control is absent.

- **Unauthenticated expensive work** — trigger: an `internet-facing` Process receiving flows with `authentication: none` or `unknown` whose `data_description` implies non-trivial computation (queries, uploads, generation, model inference). Attackers spend one cheap request per unit of the defender's CPU, memory, or per-call cost — asymmetry needs no botnet (OWASP LLM10 for inference endpoints).
- **Unbounded input** — trigger: inbound flows whose `data_description` includes user-supplied payloads with no size/complexity bounds stated. Oversized bodies, deeply nested documents, zip bombs, pathological regexes (ReDoS), and billion-laughs XML turn a single request into minutes of processing or an out-of-memory crash.
- **Storage and quota exhaustion** — trigger: a Data Store fed by flows originating from a less-trusted zone with `authentication` none/`unknown` and no indication of write quotas. Unbounded inserts, log floods, or large-object uploads consume disk, budget, or hard provider quotas until legitimate writes fail.
- **Connection and pool starvation** — trigger: multiple Processes sharing one Data Store, or a Process whose `technology` implies bounded worker/connection pools, reachable by unauthenticated flows. Slow-loris-style held connections or bursts pin the pool; every sharer of the resource fails together.
- **Lockout as a weapon** — trigger: flows whose `authentication` indicates lockout-style protections (password + retry limits) on entities with `kind: human`. An attacker deliberately exhausts retry budgets against known usernames, denying real users their own accounts — the defense becomes the attack surface.
- **Amplification relay** — trigger: a Process that, on one inbound request, emits larger or multiple outbound flows (fan-out, callbacks, notifications) to targets named in the request or reachable across a boundary. The attacker aims your infrastructure's bandwidth and reputation at a victim — or back at your own interior services.
- **Dependency chokepoint** — trigger: an element that many flows converge on (a shared gateway, auth service, or store) — especially one carrying `availability-critical` tags or sitting on every derived boundary crossing's path. Its outage is every dependent's outage; attackers target the convergence point, not the fan.
- **Poison input crash loop** — trigger: a queue-shaped store or flow (per `technology`/`protocol`) with consumers and no indication of dead-lettering. One malformed message crashes the consumer, is redelivered, crashes it again — a single payload becomes a standing outage.

## Guardrails

- **Second-order reach.** Availability failures cascade along dependency edges: a starved store takes down its processes; a dead auth service takes down everything that authenticates. After each threat, walk the model's flows *inbound to the victim* — who depends on this element? — and score impact on the full dependent set (circuit-breaker absence makes cascades the default, OWASP ASI08). Also flag security-relevant degradation: does the system fail open when this element dies?
- **Attacker perspective.** Name the resource exhausted and the asymmetry: *which* element ID, *which* resource (CPU, pool, disk, quota, budget), *via which* flow, and why the attack costs less than the defense. "No rate limiting" is an observation; "an unauthenticated attacker drives `flow:internet-to-search:query` with pathological queries, pinning `process:search` CPU and starving all users" is a threat.
- **Unknowns are findings, not assumptions.** Rate limits, quotas, and timeouts are almost never stated in system descriptions. Phrase these threats conditionally on the `unknown`/silent attribute and let the critic hold them needs-info — do not assert their absence, and do not skip the threat because the control *might* exist.
- **Stay in the model.** Reference only element IDs the System Model contains. Do not invent CDNs, WAFs, or autoscaling — and do not credit them either; capacity you cannot see in the model is `unknown`, not infinite.

## Mitigations

Tie each mitigation to the pattern it addresses; prefer changes visible in the model's own attributes.

- *Unauthenticated expensive work*: authenticate before expensive paths; per-client rate limits and cost budgets keyed to verified identity; proof-of-work or CAPTCHA only as a last resort on truly anonymous endpoints.
- *Unbounded input*: hard request-size caps at the edge, parse-depth and entity-expansion limits, linear-time regex engines or timeouts, and streaming parsers with quotas — reject before allocating.
- *Storage and quota exhaustion*: per-principal write quotas, TTL/retention on unbounded collections, and alerting on fill-rate anomalies; budget alarms where storage is metered cost.
- *Connection and pool starvation*: aggressive idle/read timeouts, per-source connection caps, bounded pools with load-shedding (fail fast over queueing forever), and bulkheads separating tenants or callers.
- *Lockout as a weapon*: prefer progressive delays and risk-based challenges over hard lockouts; scope counters to source+account pairs so one attacker cannot lock accounts they do not control.
- *Amplification relay*: authenticate and authorize callback/fan-out targets against an allowlist; cap fan-out per request; sign and verify destinations rather than accepting caller-supplied URLs.
- *Dependency chokepoint*: circuit breakers and timeouts on every edge into the chokepoint; graceful degradation paths that fail closed for security decisions; capacity headroom and horizontal scaling for elements tagged `availability-critical`.
- *Poison input crash loop*: dead-letter queues with bounded redelivery, schema validation before processing, and consumer-side crash isolation so one message cannot wedge the group.
