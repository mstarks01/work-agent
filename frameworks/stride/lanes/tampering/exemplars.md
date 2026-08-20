# Tampering Exemplars

Three drafts against the exemplar system, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: transfer instructions modified in flight

`flow:web-api-to-ledger-service:post-transfer` carries `encryption_in_transit: none` and `authentication: none`, and appears in the derived crossings (dmz → core). Its `data_description` says what an alteration is worth: transfer instructions with customer IDs.

All three facts are catalogued — two `absent:` rows and the crossing — so this draft quotes nothing. The submitter's own sentence says the same thing, and citing it as well would be one fact filed twice.

Keep the lane straight. Speaking *as* the web API is spoofing; altering the message an honest caller sent is yours. Both can be true of one path — write only the modification here, and score it on the integrity loss.

```json
{
  "sequence": 1,
  "title": "On-path modification of transfer instructions between web API and ledger",
  "description": "`flow:web-api-to-ledger-service:post-transfer` moves gRPC transfer instructions from `boundary:dmz` into `boundary:core` with `encryption_in_transit: none`. An attacker positioned on that path — a compromised sidecar, a node in the dmz, or anything that can redirect traffic — rewrites the amount, the destination account, or the customer ID in a message the ledger has no way to distinguish from the original, since the flow also carries `authentication: none`. Second-order: `process:ledger-service` commits the altered instruction to `store:accounts-db`, so the modification becomes an authoritative balance, and `flow:ledger-service-to-audit-log:append-transfer-record` records the forged version as fact.",
  "affected_element_ids": [
    "flow:web-api-to-ledger-service:post-transfer",
    "process:ledger-service",
    "store:accounts-db"
  ],
  "verb": "alter-in-transit",
  "evidence_refs": [
    "crossing:flow:web-api-to-ledger-service:post-transfer",
    "absent:flow:web-api-to-ledger-service:post-transfer:authentication",
    "absent:flow:web-api-to-ledger-service:post-transfer:encryption_in_transit"
  ],
  "quotes": [],
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
  "sequence": 2,
  "title": "A leaked static database password grants direct writes to every balance",
  "description": "`flow:ledger-service-to-accounts-db:read-write-balances` uses a shared static password from an environment variable with full read/write scope. Possession is authority: an attacker who obtains it from a crash dump, an image layer, a log line, or a compromised `process:ledger-service` writes to `store:accounts-db` directly, bypassing whatever validation the ledger applies. Balances, account-holder records, and transaction rows can be rewritten arbitrarily. Second-order: the corruption is invisible to `store:audit-log`, because writes made outside `process:ledger-service` never traverse `flow:ledger-service-to-audit-log:append-transfer-record`, so reconciliation against the audit trail cannot detect them.",
  "affected_element_ids": [
    "store:accounts-db",
    "flow:ledger-service-to-accounts-db:read-write-balances",
    "process:ledger-service"
  ],
  "verb": "alter",
  "evidence_refs": [],
  "quotes": [
    {
      "text": "a single shared password out of an environment variable, and that account has full read/write on every table",
      "source_label": "Payments platform notes"
    }
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

## Unknown-conditional, in system B: unverified authorization on the topic

Written against exemplar system B. The submitter's own words are the trigger here — an admitted gap, recorded as `authentication: unknown` on the internal subscription rather than as an absent control. Condition the threat on the attribute and quote the admission; asserting "nothing checks topic access" would state a fact the model does not contain.

```json
{
  "sequence": 3,
  "title": "Fabricated readings enter the pipeline if topic access is unchecked",
  "description": "`flow:mqtt-broker-to-stream-processor:consume-topic` carries `authentication: unknown` and crosses from `boundary:ingest` into `boundary:platform`. If that unknown resolves to no check on who may attach to a topic, then any party who reaches `process:mqtt-broker` can publish onto the topic `process:stream-processor` consumes, and the processor treats the arriving payloads as gateway telemetry because they came off the expected topic. The attacker modifies the fleet's picture rather than reading it: suppressed alarm thresholds, invented readings, altered volumes written on into `store:telemetry-store` as though a device had reported them. This draft is conditional on that flow's `authentication` attribute; it is not a claim that topic authorization is missing.",
  "affected_element_ids": [
    "process:mqtt-broker",
    "process:stream-processor",
    "flow:mqtt-broker-to-stream-processor:consume-topic",
    "store:telemetry-store"
  ],
  "verb": "forge",
  "evidence_refs": [
    "crossing:flow:mqtt-broker-to-stream-processor:consume-topic",
    "unknown:flow:mqtt-broker-to-stream-processor:consume-topic:authentication"
  ],
  "quotes": [
    {
      "text": "I could not tell you what, if anything, checks that a subscriber is allowed on a topic",
      "source_label": "Fleet telemetry platform notes"
    }
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium and conditional on the unknown `authentication` value: `process:mqtt-broker` is `internet-facing` so reaching it is cheap, but the control may in fact be present. Impact is high: falsified readings persist in `store:telemetry-store`, classified confidential and tagged `business-critical-data`, and are acted on as genuine."
  },
  "mitigations": [
    {
      "summary": "Record the topic authorization model, then enforce it",
      "detail": "Establish what governs publish and subscribe on `flow:mqtt-broker-to-stream-processor:consume-topic`; if nothing does, restrict each credential to its own fleet's topics."
    },
    {
      "summary": "Make the processor verify what it consumed",
      "detail": "Have `process:stream-processor` accept only payloads signed by a device credential, so topic position alone does not make a reading authentic."
    }
  ]
}
```
