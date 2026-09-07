# Data Protection Exemplars

Two drafts against exemplar system A. Classification is one of the few things the model states outright, and the first draft shows what a stated label still leaves open; the second is a browser-facing requirement that applies because a browser exists.

## V14.1.1 — Whether every sensitive data item has been classified is never stated

Two stores carry a label. The data in motion carries none, and nothing says the two labels came out of an exercise that covered everything.

```json
{
  "requirement": "1.1",
  "needs_evidence": "people",
  "title": "Whether every sensitive data item has been classified is never stated",
  "description": "V14.1.1 asks that every piece of sensitive data the application creates or handles has been identified and sorted into a protection level, with the regulation that applies to it taken into account. The model states a level for two stores: `store:accounts-db` is confidential and tagged `pii` and `financial`, and `store:audit-log` is internal. It states none for the data in motion — the payment instructions on `flow:customer-to-web-api:submit-payment` and the transfer records carrying customer IDs on `flow:web-api-to-ledger-service:post-transfer` — and nothing says the two store labels came out of an exercise that covered everything the system touches. The requirement applies and the input does not settle it; the classification record the organisation keeps would.",
  "affected_element_ids": [
    "store:accounts-db",
    "store:audit-log",
    "flow:customer-to-web-api:submit-payment",
    "flow:web-api-to-ledger-service:post-transfer"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment",
    "crossing:flow:web-api-to-ledger-service:post-transfer"
  ],
  "quotes": []
}
```
## V14.3.1 — Nothing says client-held account data is cleared when the customer session ends

A browser-facing requirement, applying on the same evidence chapter V3 uses. Its subject is the end of the session, not the caching of responses during it.

```json
{
  "requirement": "3.1",
  "needs_evidence": "code",
  "title": "Nothing says client-held account data is cleared when the customer session ends",
  "description": "V14.3.1 asks that data a client holds while authenticated — in the browser's document or its storage — is cleared once the client or the session is terminated, whether the server drives that with a header such as Clear-Site-Data or the client does it itself when the server cannot be reached. It applies here because `entity:customer` is a human reaching `process:web-api` over HTTPS with a session cookie, so a browser holds responses carrying account identifiers and payment instructions. The notes describe the exchange and say nothing about what happens to that data at logout or expiry. The requirement applies and the input does not settle it; the client-side code and the headers `process:web-api` sends at session end would.",
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
