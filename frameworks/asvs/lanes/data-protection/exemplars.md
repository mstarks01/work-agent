# Data Protection Exemplars

Two drafts against exemplar system A. Classification is one of the few things the model states outright, so the first draft cites it directly; the second is a browser-facing requirement that applies because a browser exists.

## V14.1.1 — No handling rules follow from the accounts database classification

The store's classification and its asset tags are stated. What follows from them is not.

```json
{
  "requirement": "1.1",
  "needs_evidence": "people",
  "title": "No handling rules follow from the accounts database classification",
  "description": "V14.1.1 asks that data is classified and that controls follow from the classification. It applies here because `store:accounts-db` is classified confidential and tagged `pii` and `financial`, so the first half is answered by the model. The second half is not: the notes describe what the ledger service does with the data and never describe a handling rule that follows from the classification — no retention period, no masking, no restriction tied to the label. The requirement applies and the input does not settle it.",
  "affected_element_ids": [
    "store:accounts-db"
  ],
  "evidence_refs": [
    "unknown:store:accounts-db:encryption_at_rest"
  ],
  "quotes": []
}
```

## V14.3.1 — Client-side caching of account data is never constrained

A browser-facing requirement, applying on the same evidence chapter V3 uses. Where no browser existed, this would be the exclusion instead.

```json
{
  "requirement": "3.1",
  "needs_evidence": "config",
  "title": "Client-side caching of account data is never constrained",
  "description": "V14.3.1 asks that sensitive data is not cached or stored by the client. It applies here because `entity:customer` is a human reaching `process:web-api` over HTTPS with a session cookie, so a browser holds the responses, and those responses carry account identifiers and payment instructions. The notes describe the exchange and say nothing about cache directives or client-side storage. The requirement applies and the input does not settle it.",
  "affected_element_ids": [
    "entity:customer",
    "process:web-api",
    "flow:customer-to-web-api:submit-payment"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment"
  ],
  "quotes": []
}
```
