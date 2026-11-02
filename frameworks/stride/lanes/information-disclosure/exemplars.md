# Information Disclosure Exemplars

Four drafts against the exemplar systems, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: plaintext transfer traffic between zones

`flow:process:web-api>process:ledger-service>post-transfer` has `encryption_in_transit: none`, appears in the derived crossings (dmz → core), and carries transfer instructions with customer IDs. Stated trigger, stated content, stated crossing.

Same path, two lanes: rewriting those messages is tampering, reading them is yours. Write the read harm only, and score it on what is exposed rather than on what could be changed.

```json
{
  "sequence": 1,
  "title": "Transfer instructions readable on the wire between the web API and the ledger",
  "description": "`flow:process:web-api>process:ledger-service>post-transfer` carries gRPC transfer instructions — customer identifiers, amounts, destination accounts — from `boundary:dmz` into `boundary:core` with `encryption_in_transit: none`. Anyone able to observe that path (a compromised node in either zone, a misconfigured span port, a sidecar with packet capture) reads the payment activity of every customer in real time, without needing to authenticate to anything. Second-order: the observed identifiers are the inputs the ledger uses against `store:accounts-db`, so a passive observer accumulates the account mapping needed to target specific customers in later attacks.",
  "affected_element_ids": [
    "flow:process:web-api>process:ledger-service>post-transfer",
    "process:web-api",
    "process:ledger-service"
  ],
  "verb": "intercept",
  "evidence_refs": [
    "crossing:flow:process:web-api>process:ledger-service>post-transfer",
    "absent:flow:process:web-api>process:ledger-service>post-transfer:encryption_in_transit"
  ],
  "quotes": [],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium: passive observation is cheap and needs no credential, but the attacker must reach the dmz-to-core path. Impact is high: the flow exposes `pii` and `financial` detail for every customer transaction, continuously and irreversibly once captured."
  },
  "mitigations": [
    {
      "summary": "Require TLS on the ledger call",
      "detail": "Set `encryption_in_transit` on `flow:process:web-api>process:ledger-service>post-transfer` to mTLS, so the payload is confidential and the peer is verified in one step."
    },
    {
      "summary": "Minimize identifiers on the wire",
      "detail": "Carry opaque per-transaction references rather than raw account identifiers where the ledger can resolve them itself."
    }
  ]
}
```

## Second-order: one leaked credential exposes the whole corpus

`flow:process:ledger-service>store:accounts-db>read-write-balances` authenticates with a shared static password from an environment variable. The exemplar is about scale: the credential itself is a small thing, and reading it is a small event, but what it unlocks is every account holder's record at once.

```json
{
  "sequence": 2,
  "title": "A leaked static database password exposes every account holder's records",
  "description": "The shared static password on `flow:process:ledger-service>store:accounts-db>read-write-balances` lives in an environment variable of `process:ledger-service` and grants full read access to `store:accounts-db`. Environment variables surface in crash dumps, process listings, error pages, container image layers, and log output, so disclosure does not require compromising the process outright. An attacker holding the password queries the store directly from anywhere in `boundary:core`, reading balances and account-holder PII in bulk. Second-order: this is corpus-scale rather than per-request disclosure — one small leak yields the entire confidential dataset, and the read leaves no trace in `store:audit-log`, which only records transfers made through the ledger.",
  "affected_element_ids": [
    "store:accounts-db",
    "flow:process:ledger-service>store:accounts-db>read-write-balances",
    "process:ledger-service"
  ],
  "verb": "recover-credential",
  "evidence_refs": [],
  "quotes": [
    {
      "text": "that account has full read/write on every table",
      "source_label": "Payments platform notes"
    }
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium: a static, long-lived secret in the environment has many disclosure paths, though the attacker must obtain it first. Impact is high: `store:accounts-db` is classified confidential and tagged `pii` and `financial`, and bulk disclosure is irreversible and regulator-reportable."
  },
  "mitigations": [
    {
      "summary": "Replace the static password with short-lived credentials",
      "detail": "Use IAM database authentication or a secret manager with rotation, so a disclosed value expires rather than persisting as standing read access."
    },
    {
      "summary": "Grant read scope narrowly and detect bulk reads",
      "detail": "Limit the ledger's grant to the rows and columns its operations need, and alert on query volumes inconsistent with transfer traffic."
    }
  ]
}
```

## Unknown-conditional, in system B: unverified encryption at rest, shared tenancy

Written against exemplar system B, and worth comparing with the canonical draft above: the same `encryption_at_rest: unknown` trigger, but the multi-tenant store changes what one copy costs. Note the boundary the skill draws — disk encryption does nothing against an attacker with valid query access, so the conditional threat is about the store's shadow copies.

```json
{
  "sequence": 3,
  "title": "One snapshot exposes every tenant if the telemetry store is unencrypted at rest",
  "description": "`store:telemetry-store` is classified confidential and tagged `business-critical-data`, and its `encryption_at_rest` attribute is `unknown`. If that unknown resolves to unencrypted storage, anyone obtaining a copy of the media — an exported snapshot, a backup with broader read access, a replica in a lower environment — reads the readings directly, without presenting a credential to `process:stream-processor` or touching the platform's own access path. What makes this worse than the same gap on a single-tenant store is that the partitions share one space: the tenant key separates customers inside a live query, and a raw copy carries no query, so one exported artifact discloses every fleet's operating data at once. This draft is conditional on the `encryption_at_rest` attribute; it is not a claim that the store is unencrypted.",
  "affected_element_ids": [
    "store:telemetry-store"
  ],
  "verb": "read",
  "evidence_refs": [
    "unknown:store:telemetry-store:encryption_at_rest"
  ],
  "quotes": [
    {
      "text": "Nobody has confirmed whether that store is encrypted at rest.",
      "source_label": "Fleet telemetry platform notes"
    }
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium and conditional on the unknown `encryption_at_rest` value: snapshot and backup sprawl needs no live access, but the control may in fact be present. Impact is high: a raw copy of a confidential store tagged `business-critical-data` discloses every tenant's fleet at once, and disclosure is irreversible."
  },
  "mitigations": [
    {
      "summary": "Record the at-rest posture, then enforce it on copies",
      "detail": "Establish whether `store:telemetry-store` is encrypted at rest; require customer-managed keys on the store and on every backup, snapshot, and replica, and restrict who may export them."
    },
    {
      "summary": "Make one copy cost one tenant",
      "detail": "Key encryption per tenant so a single exported artifact cannot be read across the whole partition space."
    }
  ]
}
```

## Contrast, in system A: a compromised reader is not a storage copy

`process:ledger-service` reads `store:accounts-db` through its own grant, and the store's `encryption_at_rest` is `unknown`. Two attackers reach the same rows. One obtains a copy of the storage: encryption at rest stops that attacker, and the draft is conditional on the unknown, as in the system B draft. The other runs code inside the ledger service and reads through the grant the service already holds: encryption at rest does nothing against that attacker, because the database decrypts each row for an authorized session. The two have different preconditions and different remedies, so they are two drafts. Where the sources say a component reads a store, write the compromised-reader draft. One draft does not stand in for the other.

```json
{
  "sequence": 4,
  "title": "Code running in the ledger service reads every account through the service's own grant",
  "description": "`process:ledger-service` reads `store:accounts-db` over `flow:process:ledger-service>store:accounts-db>read-write-balances`, and its account has full read/write on every table. An attacker who runs code in the ledger service, for example through the unauthenticated call from the internet-facing `process:web-api` on `flow:process:web-api>process:ledger-service>post-transfer`, inherits that grant and reads the balances and account-holder PII of every customer over the connection the service already uses. Encryption at rest does not stop this attacker, because the database decrypts every row for an authorized session; that is what separates this draft from a storage-copy draft on the same store. Second-order: the reads look like the service's own queries, so `store:audit-log`, which records transfers only, holds no trace of them.",
  "affected_element_ids": [
    "store:accounts-db",
    "process:ledger-service",
    "flow:process:ledger-service>store:accounts-db>read-write-balances"
  ],
  "verb": "read",
  "evidence_refs": [
    "absent:flow:process:web-api>process:ledger-service>post-transfer:authentication",
    "crossing:flow:process:web-api>process:ledger-service>post-transfer"
  ],
  "quotes": [
    {
      "text": "that account has full read/write on every table",
      "source_label": "Payments platform notes"
    }
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium: the attacker must first run code in `process:ledger-service`, but the unauthenticated call from the internet-facing web API is a stated path to it. Impact is high: `store:accounts-db` is classified confidential and tagged `pii` and `financial`, and the grant covers every table."
  },
  "mitigations": [
    {
      "summary": "Scope the ledger service's grant to its operations",
      "detail": "Replace the full read/write account with a role limited to the tables and columns that transfers need, so code in the service cannot read every account."
    },
    {
      "summary": "Make bulk reads by the service visible",
      "detail": "Log and alert on queries from the ledger service's account that read more rows than transfer traffic needs."
    }
  ]
}
```
