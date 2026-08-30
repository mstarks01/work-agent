# Secure Communication Exemplars

Two drafts against exemplar system A. This is the chapter the System Model answers best: `encryption_in_transit` is recorded per flow, and the derived crossings say which links leave a zone. Note the difference between the two drafts — one attribute is stated absent and the other is never stated.

## V12.1.1 — The transfer path to the ledger service runs unencrypted

`none` on an internal crossing. The submitter answered, so the ruling is written plainly and cites the `absent:` row rather than the sentence behind it.

```json
{
  "requirement": "1.1",
  "needs_evidence": "",
  "title": "The transfer path to the ledger service runs unencrypted",
  "description": "V12.1.1 asks that connections carrying application data use TLS at a current version. It applies to `flow:web-api-to-ledger-service:post-transfer`, which carries transfer instructions with customer identifiers and is a derived boundary crossing from `boundary:dmz` into `boundary:core`. Its `encryption_in_transit` is stated absent rather than left open, so the input settles this: the link runs without transport protection. The requirement's internal scope is not an exemption — ASVS applies it to a backend link exactly as to an internet-facing one.",
  "affected_element_ids": [
    "process:web-api",
    "process:ledger-service",
    "flow:web-api-to-ledger-service:post-transfer"
  ],
  "evidence_refs": [
    "absent:flow:web-api-to-ledger-service:post-transfer:encryption_in_transit",
    "crossing:flow:web-api-to-ledger-service:post-transfer"
  ],
  "quotes": []
}
```

## V12.2.1 — Transport protection on the database link is never stated

The same requirement family against a flow whose attribute is `unknown`. The remedy differs — one is a fix, the other is a question — so the two are never written the same way.

```json
{
  "requirement": "2.1",
  "needs_evidence": "prose",
  "title": "Transport protection on the database link is never stated",
  "description": "V12.2.1 asks that connections to backend components are protected in transit. It applies to `flow:ledger-service-to-accounts-db:read-write-balances`, which carries balances and account-holder PII over the PostgreSQL wire protocol. Its `encryption_in_transit` is never stated, which is a different fact from the stated absence on `flow:web-api-to-ledger-service:post-transfer`: here the input left the question open rather than answering it. The requirement applies and the input does not settle it.",
  "affected_element_ids": [
    "process:ledger-service",
    "store:accounts-db",
    "flow:ledger-service-to-accounts-db:read-write-balances"
  ],
  "evidence_refs": [
    "unknown:flow:ledger-service-to-accounts-db:read-write-balances:encryption_in_transit"
  ],
  "quotes": []
}
```
