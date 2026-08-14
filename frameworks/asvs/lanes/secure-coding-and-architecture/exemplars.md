# Secure Coding and Architecture Exemplars

Two drafts against exemplar system A. This chapter's subject is code, and the input is prose — the first draft states that limit plainly. The second shows the part the model *does* answer: how the system is divided.

## V15.2.1 — Defensive handling of untrusted data cannot be settled from this input

The honest ruling for a code-practice requirement. Saying the input carries prose rather than code is the answer, not an excuse.

```json
{
  "requirement": "2.1",
  "title": "Defensive handling of untrusted data cannot be settled from this input",
  "description": "V15.2.1 asks that the application handles untrusted data defensively throughout its code. It applies to this system: `process:web-api` accepts payment instructions from `entity:customer` across a boundary crossing, so untrusted data enters. The requirement's subject is the code that handles it, and this job carries a description of the system. So the requirement applies and this input cannot settle it — source access would.",
  "affected_element_ids": [
    "process:web-api"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment"
  ],
  "quotes": []
}
```

## V15.3.1 — The ledger service's own exposure is never stated

Separation is a structural fact and the model states it. The draft cites the zones rather than reasoning about code.

```json
{
  "requirement": "3.1",
  "title": "The ledger service's own exposure is never stated",
  "description": "V15.3.1 asks that components are separated so that a compromise of one is contained. The model answers part of this directly: `boundary:public-internet`, `boundary:dmz` and `boundary:core` divide the system, and `process:web-api` sits in the DMZ while `process:ledger-service` sits in the core. What is left open is `process:ledger-service`'s own `exposure`, which is never stated, so whether the core zone is actually reachable only through the DMZ is unsettled. The requirement applies and the input does not settle it.",
  "affected_element_ids": [
    "process:web-api",
    "process:ledger-service"
  ],
  "evidence_refs": [
    "unknown:process:ledger-service:exposure",
    "crossing:flow:web-api-to-ledger-service:post-transfer"
  ],
  "quotes": []
}
```
