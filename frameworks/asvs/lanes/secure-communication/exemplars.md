# Secure Communication Exemplars

Two drafts against exemplar system A. This is the chapter the System Model answers best: `encryption_in_transit` is recorded per flow, and the derived crossings say which links leave a zone. Note the difference between the two drafts — one attribute is stated absent and the other is never stated.

## V12.3.3 — The transfer path to the ledger service runs unencrypted

`none` on an internal crossing between two HTTP-based services — gRPC runs over HTTP/2. The submitter answered, so the ruling is written plainly and cites the `absent:` row rather than the sentence behind it.

```json
{
  "requirement": "3.3",
  "needs_evidence": "",
  "title": "The transfer path to the ledger service runs unencrypted",
  "description": "V12.3.3 asks that every link between the application's own HTTP-based services is encrypted in transit and never falls back to plaintext. It applies to `flow:web-api-to-ledger-service:post-transfer`, a gRPC call — HTTP/2 underneath — that carries transfer instructions with customer identifiers and is a derived boundary crossing from `boundary:dmz` into `boundary:core`. Its `encryption_in_transit` is stated absent rather than left open, so the input settles this: the link runs without transport protection. The requirement's internal scope is the point rather than an exemption — this requirement exists for exactly this kind of link.",
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
## V12.3.1 — Transport protection on the database link is never stated

A database connection is not an HTTP service, so the requirement for it is the one that names databases. The flow's attribute is `unknown`, and the remedy differs from the draft above — one is a fix, the other is a question — so the two are never written the same way.

```json
{
  "requirement": "3.1",
  "needs_evidence": "prose",
  "title": "Transport protection on the database link is never stated",
  "description": "V12.3.1 asks that every connection into and out of the application — databases, middleware, management tools and partner systems among them — runs over an encrypted protocol such as TLS with no fallback to plaintext. It applies to `flow:ledger-service-to-accounts-db:read-write-balances`, which carries balances and account-holder PII over the PostgreSQL wire protocol. Its `encryption_in_transit` is never stated, which is a different fact from the stated absence on `flow:web-api-to-ledger-service:post-transfer`: here the input left the question open rather than answering it. The requirement applies and the input does not settle it.",
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
