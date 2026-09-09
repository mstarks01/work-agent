# Validation and Business Logic Exemplars

Two drafts against exemplar system A. The second is the class this chapter produces most: a requirement that verifies a document exists, which no representation of a running system can settle.

## V2.2.2 — No enforcement side is stated for input validation on the customer path

The requirement asks which side enforces a validation rule. The model answers where the untrusted side is — `entity:customer` sits in `boundary:public-internet` — and says nothing about enforcement, so the ruling names the crossing and stays open.

```json
{
  "requirement": "2.2",
  "needs_evidence": "prose",
  "title": "No enforcement side is stated for input validation on the customer path",
  "description": "V2.2.2 asks that validation is enforced on a trusted service layer rather than on the client. It applies here because `entity:customer` is a human external entity in `boundary:public-internet` and `flow:customer-to-web-api:submit-payment` is a derived boundary crossing into `boundary:dmz`, so an untrusted side exists and it submits payment instructions. The notes describe what the customer sends and never describe any validation of it, on either side. The requirement applies and the input does not settle it; naming which element rejects a malformed transfer would settle it.",
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

## V2.1.1 — Documented input validation rules cannot be settled from this input

A documentation requirement. The subject is an artifact of the organization, not of the system, so no model can hold the answer — but the submitter does. The policy exists or it does not, and one sentence from them settles it, so ask for more description rather than sending the question to a person the job cannot reach.

```json
{
  "requirement": "1.1",
  "needs_evidence": "prose",
  "title": "Documented input validation rules cannot be settled from this input",
  "description": "V2.1.1 verifies that the application's input validation rules are documented. It applies to this system: `process:web-api` accepts payment instructions and account identifiers from `entity:customer` across a trust boundary, so there are rules to document. The subject of the requirement is a document held by the organization that built the system, and this description says nothing about it. So the requirement applies and this input does not settle it. A fuller description would: supply the validation policy, or state that none exists.",
  "affected_element_ids": [
    "process:web-api"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment"
  ],
  "quotes": []
}
```
