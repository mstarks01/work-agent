# Multi-Tenant SaaS

## When this applies

The System Model carries a Trust Boundary of `kind: tenant`, or names tenants, customers-as-organizations, workspaces or accounts as a partition of the same running system.

## What to look for

- **The tenant boundary is enforced in code, not in the network.** Unlike a network zone, a tenant boundary usually separates two requests to the same process against the same store. Ask what carries tenant identity: a claim inside a verified credential is enforceable, a header or request parameter is attacker-controlled, and a value the application looked up once and cached is a place the two diverge.
- **Every query is a cross-tenant query until scoped.** For each flow into a shared Data Store, ask whether the tenant predicate is applied by the caller (forgettable, once per query) or by the storage layer (row-level security, per-tenant credentials, separate schemas). A single unscoped report, export, admin view or background job is the whole boundary.
- **Identifier guessability.** Sequential or predictable per-object IDs make cross-tenant access a counting exercise once the scoping check is missing. This is object-level authorization, and in a tenant model its blast radius is every customer rather than one.
- **Shared infrastructure as a channel.** Caches keyed without the tenant, message queues consumed by all tenants' workers, shared search indexes and shared connection pools carry data across the boundary without any flow in the model saying so. Name the element and say which mechanism crosses.
- **Noisy neighbours.** One tenant's load reaching another's availability — a shared pool, a shared rate limit, an unbounded export — is a real denial-of-service finding in this architecture, and it is scored on the tenants who did nothing.
- **Provisioning, offboarding and impersonation.** Tenant creation, deletion and the support "log in as customer" path all cross the boundary by design; ask what authorizes and records each. Support impersonation with no per-tenant audit line is both a privilege and an attribution finding.
- **Per-tenant configuration as an attack surface.** Tenant-supplied identity providers, webhooks, templates and custom fields are one tenant's input reaching another tenant's execution or another tenant's admin screen.
- **Aggregate and cross-tenant analytics.** Anything that reads across tenants by design is the one component whose compromise is total; treat its reach explicitly.

## Guardrails

- This pack is **analysis knowledge, not evidence.** A finding still rests on the submitter's words, an `unknown` attribute, or a derived crossing.
- Do not assume isolation exists or does not. Where the model is silent about how tenant scoping is enforced, the attribute is `unknown` — write the threat conditionally and name what has to be learned.
