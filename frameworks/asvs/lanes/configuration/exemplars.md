# Configuration Exemplars

Two drafts against exemplar system A. Both rest on one sentence in the notes. The first is settled by it, because the sentence states the credential's shape outright; the second is not, because how a process reads a secret does not say where the secret is kept.

## V13.2.1 — The ledger service reaches the accounts database with one static shared password

A backend link, and the credential on it is stated outright: one password, shared, never described as rotating. That is the shape the requirement forbids, so the ruling is not conditional.

```json
{
  "requirement": "2.1",
  "needs_evidence": "",
  "title": "The ledger service reaches the accounts database with one static shared password",
  "description": "V13.2.1 asks that backend components which do not share the user session mechanism authenticate to each other with individual service accounts, short-lived tokens or certificates, and never with an unchanging credential such as a shared password or an API key. It applies here because `process:ledger-service` reaches `store:accounts-db` over `flow:ledger-service-to-accounts-db:read-write-balances`, a backend link that carries no user session. The submitter states the credential: one static password shared by everything that uses the connection. That is the shape the requirement forbids, so the input settles this and settles it without any inference about the deployment.",
  "affected_element_ids": [
    "process:ledger-service",
    "store:accounts-db",
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
## V13.3.1 — Whether a secrets manager holds the database credential is never stated

The same sentence, and a different requirement. An environment variable is how the process reads the secret; a vault can inject one and a deployment file can hard-code one, and the notes distinguish neither.

```json
{
  "requirement": "3.1",
  "needs_evidence": "config",
  "title": "Whether a secrets manager holds the database credential is never stated",
  "description": "V13.3.1 asks that backend secrets are created, stored and controlled through a secrets management solution rather than living in source or build artifacts. It applies here because `flow:ledger-service-to-accounts-db:read-write-balances` authenticates with a static password, so a backend secret exists. The submitter states how the process reads it — an environment variable on `process:ledger-service` — and not where it is kept: a vault can inject a variable and a deployment file can hard-code one, and the notes distinguish neither. The requirement applies and the input does not settle it; the deployment configuration of `process:ledger-service` would.",
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
