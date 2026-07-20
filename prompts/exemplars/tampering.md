# Tampering Exemplars

Three drafts against the exemplar system, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: transfer instructions modified in flight

`flow:web-api-to-ledger-service:post-transfer` carries `encryption_in_transit: none` and `authentication: none`, and appears in the derived crossings (dmz → core). Its `data_description` says what an alteration is worth: transfer instructions with customer IDs.

Keep the lane straight. Speaking *as* the web API is spoofing; altering the message an honest caller sent is yours. Both can be true of one path — write only the modification here, and score it on the integrity loss.

```json
{
  "id": "T-01",
  "category": "tampering",
  "title": "On-path modification of transfer instructions between web API and ledger",
  "description": "`flow:web-api-to-ledger-service:post-transfer` moves gRPC transfer instructions from `boundary:dmz` into `boundary:core` with `encryption_in_transit: none`. An attacker positioned on that path — a compromised sidecar, a node in the dmz, or anything that can redirect traffic — rewrites the amount, the destination account, or the customer ID in a message the ledger has no way to distinguish from the original, since the flow also carries `authentication: none`. Second-order: `process:ledger-service` commits the altered instruction to `store:accounts-db`, so the modification becomes an authoritative balance, and `flow:ledger-service-to-audit-log:append-transfer-record` records the forged version as fact.",
  "affected_element_ids": [
    "flow:web-api-to-ledger-service:post-transfer",
    "process:ledger-service",
    "store:accounts-db"
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium: no credential is required, but the attacker must first hold a position on the dmz-to-core path. Impact is high: altered instructions change `financial` state in `store:accounts-db` (classified confidential) and are recorded as legitimate."
  },
  "mitigations": [
    {
      "summary": "Encrypt and authenticate the ledger call",
      "detail": "Require mTLS on `flow:web-api-to-ledger-service:post-transfer` so messages are both confidential and integrity-protected end to end."
    },
    {
      "summary": "Sign transfer instructions at origin",
      "detail": "Have `process:web-api` sign the instruction payload and `process:ledger-service` verify it, so an on-path rewrite fails independently of the transport."
    }
  ]
}
```

## Second-order: shared database credential as a write primitive

`flow:ledger-service-to-accounts-db:read-write-balances` authenticates with a shared static password held in an environment variable, granting full read/write. The exemplar is about reach: one leaked secret is not a modest configuration issue, it is unmediated write access to the record of every customer's money, and every consumer downstream inherits the corruption.

```json
{
  "id": "T-02",
  "category": "tampering",
  "title": "A leaked static database password grants direct writes to every balance",
  "description": "`flow:ledger-service-to-accounts-db:read-write-balances` uses a shared static password from an environment variable with full read/write scope. Possession is authority: an attacker who obtains it from a crash dump, an image layer, a log line, or a compromised `process:ledger-service` writes to `store:accounts-db` directly, bypassing whatever validation the ledger applies. Balances, account-holder records, and transaction rows can be rewritten arbitrarily. Second-order: the corruption is invisible to `store:audit-log`, because writes made outside `process:ledger-service` never traverse `flow:ledger-service-to-audit-log:append-transfer-record`, so reconciliation against the audit trail cannot detect them.",
  "affected_element_ids": [
    "store:accounts-db",
    "flow:ledger-service-to-accounts-db:read-write-balances",
    "process:ledger-service"
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium: the credential is static and long-lived, so leakage is cheap to exploit, but the attacker must first obtain it. Impact is high: unmediated writes to a confidential store tagged `financial` and `pii`, undetectable against `store:audit-log`."
  },
  "mitigations": [
    {
      "summary": "Replace the static password with workload identity",
      "detail": "Authenticate `process:ledger-service` to `store:accounts-db` with short-lived IAM database credentials rather than a shared secret in the environment."
    },
    {
      "summary": "Scope the database grant to the ledger's actual operations",
      "detail": "Remove blanket read/write; grant only the tables and statements the transfer path needs, so a leaked credential is not full authority."
    }
  ]
}
```

## Unknown-conditional: unverified transport to the accounts database

The same flow carries `encryption_in_transit: unknown`. The model does not say the connection is plaintext — it says nobody recorded it. Write the threat conditionally, name the attribute, and let the critic mark it needs-info; asserting "the database connection is unencrypted" would state a fact the model does not contain.

```json
{
  "id": "T-03",
  "category": "tampering",
  "title": "Balance writes modifiable on the wire if database transport is unprotected",
  "description": "`flow:ledger-service-to-accounts-db:read-write-balances` carries `encryption_in_transit: unknown`. If that unknown resolves to plaintext PostgreSQL, an attacker with a position inside `boundary:core` can alter statements and result sets in flight — changing an amount as it is written, or a balance as it is read back by `process:ledger-service`. Both endpoints sit in the same zone, so this requires a prior foothold there. This draft is conditional on that attribute; it is not a claim that transport protection is absent.",
  "affected_element_ids": [
    "flow:ledger-service-to-accounts-db:read-write-balances",
    "store:accounts-db",
    "process:ledger-service"
  ],
  "severity": {
    "likelihood": "low",
    "impact": "high",
    "justification": "Likelihood is low and conditional on the unknown `encryption_in_transit` value: the path is entirely inside `boundary:core`, so a foothold is a prerequisite, and the control may in fact be present. Impact is high: modified writes land in a confidential store tagged `financial` and `pii`."
  },
  "mitigations": [
    {
      "summary": "Record and then enforce TLS on the database connection",
      "detail": "Establish the current transport setting; if it is plaintext, require TLS with server-certificate verification on `flow:ledger-service-to-accounts-db:read-write-balances`."
    }
  ]
}
```
