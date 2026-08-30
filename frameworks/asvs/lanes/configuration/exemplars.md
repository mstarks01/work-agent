# Configuration Exemplars

Two drafts against exemplar system A. Most of this chapter's subject sits outside a system description, and saying so is the honest output rather than a weak one — but the first draft shows the case where the input does carry the fact.

## V13.4.1 — The database credential is held in an environment variable

A secret in an environment variable is stated outright. The requirement is about secret management, and the input answers it.

```json
{
  "requirement": "4.1",
  "needs_evidence": "",
  "title": "The database credential is held in an environment variable",
  "description": "V13.4.1 asks that secrets are held in a secret management solution rather than in configuration or source. It applies here because `flow:ledger-service-to-accounts-db:read-write-balances` authenticates with a static password, so a secret exists. The submitter states where it lives: an environment variable on `process:ledger-service`. So the input settles this — the credential is not held in a managed secret store — and it settles it without any inference about the deployment.",
  "affected_element_ids": [
    "process:ledger-service",
    "flow:ledger-service-to-accounts-db:read-write-balances"
  ],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "a single shared password out of an environment variable",
      "source_label": "Payments platform notes"
    }
  ]
}
```

## V13.2.1 — The dependency inventory cannot be settled from this input

A dependency requirement. Its subject is an inventory the organization keeps, and no system description holds one.

```json
{
  "requirement": "2.1",
  "needs_evidence": "people",
  "title": "The dependency inventory cannot be settled from this input",
  "description": "V13.2.1 asks that third-party components are inventoried and kept current. It applies to this system: `process:web-api` is described as FastAPI on Cloud Run and `process:ledger-service` as a Python worker, so both carry third-party dependencies. The requirement's subject is an inventory the organization maintains, and this job carries a description of the system rather than that inventory. So the requirement applies and this input cannot settle it.",
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
