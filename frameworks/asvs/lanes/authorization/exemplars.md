# Authorization Exemplars

Two drafts against exemplar system A. The model answers where enforcement would have to happen, through trust zones and the derived crossings, and answers nothing about whether it does.

## V8.2.1 — Nothing restricts which operations a caller of the web API may invoke

Two callers reach the web API on different flows with different payloads, so there are functions to restrict. The notes say who calls and never say what stops one caller from invoking the other's operation.

```json
{
  "requirement": "2.1",
  "needs_evidence": "code",
  "title": "Nothing restricts which operations a caller of the web API may invoke",
  "description": "V8.2.1 asks that each function the application exposes is restricted to consumers holding explicit permission for it. It applies here because `entity:customer` and `entity:payments-provider` both reach `process:web-api` from `boundary:public-internet`, on `flow:customer-to-web-api:submit-payment` and `flow:payments-provider-to-web-api:settlement-webhook`, and each flow invokes a different operation. The notes describe both callers and never describe a check that keeps a customer session off the settlement endpoint or a webhook caller off payment submission. The requirement applies and the input does not settle it; the authorization code in `process:web-api` would.",
  "affected_element_ids": [
    "entity:customer",
    "entity:payments-provider",
    "process:web-api"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment",
    "crossing:flow:payments-provider-to-web-api:settlement-webhook"
  ],
  "quotes": []
}
```
## V8.1.1 — Documented authorization rules cannot be settled from this input

A documentation requirement again. It applies — there are rules to document, because the system distinguishes callers — and the input is a system description rather than a policy.

```json
{
  "requirement": "1.1",
  "needs_evidence": "people",
  "title": "Documented authorization rules cannot be settled from this input",
  "description": "V8.1.1 verifies that the application's authorization rules are documented. It applies to this system: `entity:customer` and `entity:payments-provider` reach `process:web-api` from `boundary:public-internet` on different flows carrying different payloads, so there are rules distinguishing what each may do. This job carries a description of the system rather than that documentation, so the requirement applies and this input cannot settle it.",
  "affected_element_ids": [
    "process:web-api"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment",
    "crossing:flow:payments-provider-to-web-api:settlement-webhook"
  ],
  "quotes": []
}
```
