# Protection at Rest, and What It Is Actually For

## When this applies

A data store states no verified protection at rest, or its `encryption_at_rest` is `unknown`, and the store holds a graded asset.

## What to look for

- **The threat model encryption at rest actually addresses.** It protects against access to the storage layer beneath the application: a lifted disk, a mounted snapshot, a copied backup, a decommissioned volume, an over-permissive storage bucket. It does almost nothing against an attacker who reaches the data through the application, because the application decrypts by design.
- **Who reaches the storage layer.** Backup jobs, replicas, snapshot exports, analytics copies and support tooling all read the same data through different doors, and those doors frequently have different controls from the primary path. The copies are where at-rest exposure usually happens.
- **Key custody.** Provider-managed keys protect against a stolen disk and not against a compromised account that can call decrypt. Ask who holds the keys, who can use them, and whether that is the same principal who holds the data.
- **What is in the store, precisely.** "Customer data" is not a finding. Credentials, tokens, personal data, financial records and anything with a regulatory character each imply a different consequence, and the model's own asset tags are the place to start.
- **Derived and incidental copies.** Caches, search indexes, message queues holding payloads, log records containing request bodies and exported reports often inherit the sensitive content and rarely inherit the protection.

## Guardrails

- Analysis knowledge, not evidence. Ground the finding in the store's own `unknown` attribute or in what the submitter wrote.
- Do not claim encryption is absent when the model says `unknown`; write the threat conditionally and let the critic rule it needs-info.
- Reading data you should not is information disclosure. If the same gap lets an attacker *change* stored data, that is a tampering finding for the tampering lane.
