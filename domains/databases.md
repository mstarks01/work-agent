# Databases and Data Stores

## When this applies

The System Model carries a Data Store whose `technology` names a database, object store, cache or search index — relational or otherwise.

## What to look for

- **The application account is the real privilege boundary.** A single account with full read/write on every table means any injection, any deserialization bug, and any compromise of the calling process is total. Ask what the account can do beyond what the caller's job requires: table scope, write versus read, schema changes, and whether it can read its own audit trail.
- **Injection reaches the store through the caller.** Concatenated queries, dynamic search filters, ORM escape hatches, and NoSQL operator injection all present as a flow whose `data_description` carries caller-supplied text. The finding belongs to the store's authority, not to the string.
- **Protection at rest is a question with two halves.** `encryption_at_rest: unknown` on a store carrying `pii` or `financial` is a needs-info finding; where encryption is stated, ask who holds the key, because provider-managed encryption defends against a stolen disk and against nothing that arrives through the application.
- **Backups, replicas and exports inherit the data and rarely the controls.** Snapshots, read replicas, analytics copies, CSV exports and developer dumps are all the same records under weaker protection, and the model often names none of them. Where the model is silent, say so against the store rather than inventing a backup element.
- **Credentials on the connection.** A static password in an environment variable, shared across services, is a shared machine identity: leak once, impersonate every consumer, and no audit line distinguishes them. Short-lived or workload-federated credentials are the mitigation to name.
- **Public or default exposure.** Object stores default to a policy someone changed; caches and search indexes ship with no authentication at all and are the classic internet-exposed data store. A store in an internal zone with `authentication: unknown` is worth asking about for exactly this reason.
- **Availability shape.** Connection pools, unindexed queries and lock contention convert one slow caller into a system-wide stall — the pool is a shared dependency even where the model draws only one flow into it.
- **Retention and audit.** Data kept past its purpose is impact an attacker gets for free; a store the application account can rewrite cannot serve as an audit record.

## Guardrails

- This pack is **analysis knowledge, not evidence.** Ground the finding in the submitter's words, an `unknown` attribute, or a derived crossing — never in a pattern named here.
- Do not invent stores, replicas or backups the model does not contain. Name the gap against the store that is there.
