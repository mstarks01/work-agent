# Information Disclosure Exemplars

Three drafts against the exemplar system, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: plaintext transfer traffic between zones

`flow:web-api-to-ledger-service:post-transfer` has `encryption_in_transit: none`, appears in the derived crossings (dmz → core), and carries transfer instructions with customer IDs. Stated trigger, stated content, stated crossing.

Same path, two lanes: rewriting those messages is tampering, reading them is yours. Write the read harm only, and score it on what is exposed rather than on what could be changed.

```json
{
  "id": "I-01",
  "category": "information-disclosure",
  "title": "Transfer instructions readable on the wire between the web API and the ledger",
  "description": "`flow:web-api-to-ledger-service:post-transfer` carries gRPC transfer instructions — customer identifiers, amounts, destination accounts — from `boundary:dmz` into `boundary:core` with `encryption_in_transit: none`. Anyone able to observe that path (a compromised node in either zone, a misconfigured span port, a sidecar with packet capture) reads the payment activity of every customer in real time, without needing to authenticate to anything. Second-order: the observed identifiers are the inputs the ledger uses against `store:accounts-db`, so a passive observer accumulates the account mapping needed to target specific customers in later attacks.",
  "affected_element_ids": [
    "flow:web-api-to-ledger-service:post-transfer",
    "process:web-api",
    "process:ledger-service"
  ],
  "grounds": [
    {
      "kind": "quote",
      "text": "not authenticated and not encrypted",
      "source_label": "Payments platform notes"
    },
    {
      "kind": "derived-fact",
      "flow_id": "flow:web-api-to-ledger-service:post-transfer"
    }
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium: passive observation is cheap and needs no credential, but the attacker must reach the dmz-to-core path. Impact is high: the flow exposes `pii` and `financial` detail for every customer transaction, continuously and irreversibly once captured."
  },
  "mitigations": [
    {
      "summary": "Require TLS on the ledger call",
      "detail": "Set `encryption_in_transit` on `flow:web-api-to-ledger-service:post-transfer` to mTLS, so the payload is confidential and the peer is verified in one step."
    },
    {
      "summary": "Minimize identifiers on the wire",
      "detail": "Carry opaque per-transaction references rather than raw account identifiers where the ledger can resolve them itself."
    }
  ]
}
```

## Second-order: one leaked credential exposes the whole corpus

`flow:ledger-service-to-accounts-db:read-write-balances` authenticates with a shared static password from an environment variable. The exemplar is about scale: the credential itself is a small thing, and reading it is a small event, but what it unlocks is every account holder's record at once.

```json
{
  "id": "I-02",
  "category": "information-disclosure",
  "title": "A leaked static database password exposes every account holder's records",
  "description": "The shared static password on `flow:ledger-service-to-accounts-db:read-write-balances` lives in an environment variable of `process:ledger-service` and grants full read access to `store:accounts-db`. Environment variables surface in crash dumps, process listings, error pages, container image layers, and log output, so disclosure does not require compromising the process outright. An attacker holding the password queries the store directly from anywhere in `boundary:core`, reading balances and account-holder PII in bulk. Second-order: this is corpus-scale rather than per-request disclosure — one small leak yields the entire confidential dataset, and the read leaves no trace in `store:audit-log`, which only records transfers made through the ledger.",
  "affected_element_ids": [
    "store:accounts-db",
    "flow:ledger-service-to-accounts-db:read-write-balances",
    "process:ledger-service"
  ],
  "grounds": [
    {
      "kind": "quote",
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

## Unknown-conditional: unverified encryption at rest

`store:accounts-db` carries `encryption_at_rest: unknown`. Note the boundary the skill draws: disk encryption does nothing against an attacker with valid query access, so the conditional threat is specifically about the store's shadow copies — backups, snapshots, replicas, decommissioned media.

```json
{
  "id": "I-03",
  "category": "information-disclosure",
  "title": "Account data exposed through backups if the store is unencrypted at rest",
  "description": "`store:accounts-db` is classified confidential and tagged `pii` and `financial`, but its `encryption_at_rest` attribute is `unknown`. If that unknown resolves to unencrypted storage, anyone who obtains a copy of the underlying media — a snapshot exported to a less-protected project, a backup bucket with broader read access, a replica in another environment, decommissioned disks — reads the full dataset without touching `process:ledger-service` or presenting any database credential. Shadow copies routinely inherit the data but not the access controls of the live store. This draft is conditional on the `encryption_at_rest` attribute; it is not a claim that the store is unencrypted.",
  "affected_element_ids": [
    "store:accounts-db"
  ],
  "grounds": [
    {
      "kind": "unknown-attribute",
      "element_id": "store:accounts-db",
      "attribute": "encryption_at_rest"
    }
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium and conditional on the unknown `encryption_at_rest` value: backup and snapshot sprawl is common and needs no live access, but the control may in fact be present. Impact is high: a full copy of a confidential store tagged `pii` and `financial` is irreversible disclosure at corpus scale."
  },
  "mitigations": [
    {
      "summary": "Record the at-rest posture, then enforce it on copies",
      "detail": "Establish whether `store:accounts-db` is encrypted at rest; require customer-managed keys on the store and on every backup, snapshot, and replica, and restrict who may export them."
    }
  ]
}
```
