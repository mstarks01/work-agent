# Repudiation

## Scope

Repudiation is the R in STRIDE: an actor performs an action and can later plausibly deny having performed it, because the system cannot produce trustworthy evidence to the contrary. The security property violated is **non-repudiation** (accountability). Your lane covers missing audit trails, logs that fail to capture actor identity or the disputed action, evidence that can be altered or deleted by the very parties it should hold accountable, and identity schemes too weak for a log entry to prove anything.

Lane boundaries with the other five categories:

- Acting under someone else's identity is **spoofing**; your concern is that weak identity makes the resulting *records* unattributable — enumerate the evidence gap, not the impersonation.
- Tampering with business data is **tampering**; modifying or deleting *audit records* is yours — the harmed property is accountability, not the data's own integrity.
- Reading logs that leak sensitive content is **information disclosure** (note it for that lane; do not claim it).
- Flooding a log pipeline to drown or drop entries is availability harm to evidence — claim it when the point is destroying accountability; pure service outage is **denial of service**.
- An attacker abusing admin rights to purge logs got those rights via **elevation of privilege**; the purgeable-evidence design is yours.

## Applicability

Your analysis targets are External Entities, Processes, and Data Stores. You receive the whole System Model and all derived boundary crossings: every element in it is available as evidence, and you should read whatever you need to ground a threat. What is scoped is where you may *file* one — a threat must name one of your targets as its affected element.

- **External Entity** — for each entity, ask: if this actor disputed an action tomorrow, what evidence exists? `kind: human` raises fraud and dispute scenarios (denied transactions, denied approvals); `kind: external-system` raises partner-integration disputes (denied API calls, denied deliveries). The `authentication` on the entity's flows determines whether log entries can bind actions to the actor at all — shared or absent credentials make every record deniable.
- **Process** — does the process record its security-relevant actions (authentication events, authorization denials, writes to sensitive stores, administrative changes), with actor, timestamp, and outcome? A process performing high-value operations (flows touching `financial`, `credentials`, `health` assets) with no logging store among its outbound flows is the classic gap. Processes that act on behalf of callers must propagate the *original* actor identity, or downstream records attribute everything to the service account.
- **Data Store** — two views. (1) Stores holding audit/log data: who can write, alter, or delete them? Evidence writable by the actors it implicates is not evidence. (2) Business stores holding disputed-action targets (`financial`, `business-critical-data` tags): is there any record of *who* changed a row, or only the row's current state?

## Threat Patterns

Each pattern names its trigger in the System Model attribute vocabulary. `unknown` means unverified — enumerate conditionally and flag the gap; never assert the control is absent.

- **No evidence trail** — trigger: a Process whose flows touch `financial`, `credentials`, `health`, or `business-critical-data` assets, with no flow in the model toward any logging or audit store. Security-relevant actions leave no record (OWASP A09); every dispute resolves in the actor's favor.
- **Unattributable actions** — trigger: flows with `authentication: none`, `unknown`, or a shared/static mechanism, feeding processes that write to sensitive stores. Even perfect logs cannot bind an action to an individual when the credential is shared or absent — "someone with the key" identifies no one.
- **Mutable audit store** — trigger: a Data Store whose `data_description`/name indicates logs or audit records, writable (inbound flows) by the same processes or zones whose actions it records, or with `authentication: unknown` on write paths. Implicated actors can rewrite history; deletion is the first post-compromise step.
- **Lost origin behind a service hop** — trigger: a chain where an entity's flow terminates at a front-end Process and a second flow continues inward under the service's own identity (`authentication` naming a service credential). Downstream records attribute user actions to the service account; individual accountability ends at the first hop.
- **Undisputed high-value writes** — trigger: a Data Store tagged `financial` or `business-critical-data` with no versioning or change-history indication anywhere in the model, reachable by multiple writers. The store shows current state only; who changed what, when, is unrecoverable in a dispute.
- **Clock and ordering ambiguity** — trigger: multiple Processes across `trust_zone`s writing to a shared audit target with nothing indicating time synchronization. Unordered or skewed timestamps let an actor contest sequence-dependent evidence ("the approval came after the transfer").
- **Externally deniable integration** — trigger: an `external-system` entity with mutual flows whose `authentication` lacks signing (bare API keys, no request signatures). Neither side can prove the other sent a specific message — disputes over deliveries, orders, or callbacks become word against word.

## Guardrails

- **Second-order reach.** Missing evidence is a *multiplier* on every other category: threats elsewhere become undetectable and unattributable, and incident response goes blind. When a store or process central to evidence is compromised or absent, say which other elements' accountability collapses with it — walk the flows into the audit path.
- **Attacker perspective.** Frame each threat as an actor exploiting an evidence gap: *who* denies *which action* on *which element ID*, and why the system cannot rebut them. "The service has no logging" is an observation; "a payment operator reverses transfers in `store:ledger` and denies it, since writes carry no operator identity" is a threat. The repudiating actor is often an insider or a legitimate counterparty, not an intruder.
- **Unknowns are findings, not assumptions.** Logging is rarely stated in system descriptions — expect `unknown`-shaped gaps and phrase threats conditionally, citing the silent attribute or the missing flow, for the critic to hold as needs-info. Never assert "nothing is logged" when the model is merely silent.
- **Stay in the model.** Reference only element IDs the System Model contains. Do not invent SIEMs, log pipelines, or audit tables — and equally, do not assume their absence proves negligence; the model's silence is the finding.

## Mitigations

Tie each mitigation to the pattern it addresses; prefer changes visible in the model's own attributes.

- *No evidence trail*: emit structured security-event logs (actor, action, target, timestamp, outcome) from every process handling sensitive assets, to a dedicated audit flow — auth events, authz denials, and sensitive writes at minimum (OWASP A09), with alerting on anomalies.
- *Unattributable actions*: individual, non-shared credentials on every flow feeding audited actions; include the verified identity (token subject) in each record, not a self-reported field.
- *Mutable audit store*: append-only or WORM storage, separate trust zone and separate write principal from the systems being audited; retention locks and integrity chaining (hash-linked or signed batches).
- *Lost origin behind a service hop*: propagate the originating identity through internal calls (token exchange, forwarded subject claims) so downstream records name the user, with the service identity recorded alongside.
- *Undisputed high-value writes*: versioned records or change-data-capture with before/after values and acting principal; make correction a new entry, never an overwrite.
- *Clock and ordering ambiguity*: synchronized time (NTP with monitoring) across zones; include monotonic sequence numbers or ingest-side timestamps so ordering survives clock disputes.
- *Externally deniable integration*: signed requests and callbacks (HMAC or asymmetric signatures with timestamps and nonces) retained on both sides; contractual retention of signed exchanges for the dispute window.
