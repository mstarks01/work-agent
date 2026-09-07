# Secure Coding and Architecture Exemplars

Two drafts against exemplar system A. This chapter's subject is code and the components it is built from, and the input is prose — both drafts state that limit plainly rather than reasoning past it.

## V15.2.1 — Whether the components sit inside their documented remediation windows cannot be settled from this input

The honest ruling for a component-currency requirement. The model names the frameworks and nothing about their versions, their age, or the time frame the organization wrote down for them.

```json
{
  "requirement": "2.1",
  "needs_evidence": "people",
  "title": "Whether the components sit inside their documented remediation windows cannot be settled from this input",
  "description": "V15.2.1 asks that no component in the application has outlived the update and remediation time frames the organization documented for it. It applies to this system: `process:web-api` is described as FastAPI on Cloud Run and `process:ledger-service` as a Python worker, so both carry third-party components with a version and an age. The requirement's subject is those versions against a written time frame, and this job carries a description of the system rather than a dependency list or that document. So the requirement applies and this input cannot settle it.",
  "affected_element_ids": [
    "process:web-api",
    "process:ledger-service"
  ],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "They submit payments through the web API, which is the only thing we expose to the internet.",
      "source_label": "Payments platform notes"
    }
  ]
}
```
## V15.3.1 — What the web API returns of an account record is never stated

The notes describe what a customer sends and never what comes back. Whether a response carries a whole record or the fields the customer needs is a question about response code.

```json
{
  "requirement": "3.1",
  "needs_evidence": "code",
  "title": "What the web API returns of an account record is never stated",
  "description": "V15.3.1 asks that the application returns only the fields of a data object a caller needs, rather than the whole object. It applies here because `process:web-api` serves `entity:customer` over `flow:customer-to-web-api:submit-payment` and the data behind it lives in `store:accounts-db`, a store classified confidential and tagged `pii` and `financial`. The notes describe what a customer sends and never describe what the API sends back, so whether a response carries a whole account record or the fields the customer needs is open. The requirement applies and the input does not settle it; the response serialisers in `process:web-api` would.",
  "affected_element_ids": [
    "entity:customer",
    "process:web-api",
    "store:accounts-db"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment"
  ],
  "quotes": []
}
```
