# Authorization Exemplars

Two drafts against exemplar system A. The model answers where enforcement would have to happen, through trust zones and the derived crossings, and answers nothing about whether it does.

## V8.2.1 — The ledger service holds unrestricted access to every account record

The database credential is the sharpest authorization fact this model carries: one account, full read/write on every table. That is stated, so the ruling is not conditional.

```json
{
  "requirement": "2.1",
  "title": "The ledger service holds unrestricted access to every account record",
  "description": "V8.2.1 asks that access to data is restricted to what the accessing component needs. It applies here because `process:ledger-service` reaches `store:accounts-db` over `flow:ledger-service-to-accounts-db:read-write-balances`, and that store is classified confidential and tagged `pii` and `financial`. The submitter states the answer: the connection uses one shared password and the account behind it holds full read/write on every table. So the input settles that no restriction exists at the database layer. Whether `process:ledger-service` applies its own restriction above that is not stated.",
  "affected_element_ids": [
    "process:ledger-service",
    "store:accounts-db",
    "flow:ledger-service-to-accounts-db:read-write-balances"
  ],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "that account has full read/write on every table",
      "source_label": "Payments platform notes"
    }
  ]
}
```

## V8.1.1 — Documented authorization rules cannot be settled from this input

A documentation requirement again. It applies — there are rules to document, because the system distinguishes callers — and the input is a system description rather than a policy.

```json
{
  "requirement": "1.1",
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
