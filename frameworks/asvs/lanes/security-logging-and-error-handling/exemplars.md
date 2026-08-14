# Security Logging and Error Handling Exemplars

Two drafts against exemplar system A. This chapter carries no level 1 requirement, so a run at level 1 rules on nothing here and you are shown nothing to rule on. At level 2 and above, an audit store exists in this model and the drafts rest on it.

## V16.2.1 — Audit records do not identify the acting customer

The audit record is described and its content is stated to be incomplete. That is the submitter answering, so the ruling is plain.

```json
{
  "requirement": "2.1",
  "title": "Audit records do not identify the acting customer",
  "description": "V16.2.1 asks that security-relevant events are logged with enough detail to identify who acted. It applies here because `flow:ledger-service-to-audit-log:append-transfer-record` writes every transfer to `store:audit-log`, so an audit trail exists. The submitter states what it contains: the entry names the ledger service and never the customer. So the input settles that the record does not identify the acting party, and it settles it without any inference about the logging library.",
  "affected_element_ids": [
    "process:ledger-service",
    "store:audit-log",
    "flow:ledger-service-to-audit-log:append-transfer-record"
  ],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "Every transfer is written to the audit bucket, but the entry names the ledger service and never the customer.",
      "source_label": "Payments platform notes"
    }
  ]
}
```

## V16.5.1 — Error handling behaviour is never described

Error handling is a requirement that applies to every system and that a working-system description never reaches.

```json
{
  "requirement": "5.1",
  "title": "Error handling behaviour is never described",
  "description": "V16.5.1 asks that the application handles an unexpected error without revealing internal detail and without failing open. It applies to this system: `process:web-api` is `internet-facing` and accepts payment instructions, so errors reach an untrusted caller. The notes describe the working path and never describe what a caller sees when the ledger service is unavailable or the database rejects a write. The requirement applies and the input does not settle it.",
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
