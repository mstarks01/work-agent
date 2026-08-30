# Encoding and Sanitization Exemplars

Two drafts against exemplar system A, the payments platform. Follow the reasoning, not the wording.

Exemplar system B is not used here. Its flows speak MQTT and a time-series write API, so its precondition refutes ASVS outright and no lane of this framework ever runs against it. That is worth knowing: the tier-0 question is settled before you are asked anything.

## V1.2.4 — Query construction against the accounts database is undescribed

The store's technology is named outright, so the requirement about parameterized queries applies. What the input never says is how the query strings are built — and that gap, not a suspicion about the code, is the ruling.

```json
{
  "requirement": "2.4",
  "needs_evidence": "code",
  "title": "Query construction against the accounts database is undescribed",
  "description": "V1.2.4 asks that database queries are built with parameterization or an equivalent that separates the query from its data. The requirement applies here because `store:accounts-db` is a PostgreSQL instance and `flow:ledger-service-to-accounts-db:read-write-balances` speaks the PostgreSQL wire protocol, so `process:ledger-service` builds queries. The submitted notes describe the credential that account uses and the privileges it holds, and say nothing at all about how the SQL reaching that connection is assembled. So the requirement applies and the input does not settle it. What would settle it is one fact: whether the ledger service composes SQL by string concatenation or hands parameters to a driver.",
  "affected_element_ids": [
    "process:ledger-service",
    "store:accounts-db",
    "flow:ledger-service-to-accounts-db:read-write-balances"
  ],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "The ledger service talks to the accounts database with a single shared password out of an environment variable, and that account has full read/write on every table.",
      "source_label": "Payments platform notes"
    }
  ]
}
```

## V1.5.1 — No XML parser exists in this system

The opposite ruling. No element in this model parses XML and no flow names SOAP, so the requirement does not apply — and the draft says which stated fact decided it rather than resting on the absence of a word.

```json
{
  "requirement": "5.1",
  "needs_evidence": "",
  "title": "No XML parser exists in this system",
  "description": "V1.5.1 governs the configuration of an XML parser against external entity and schema resolution. It does not apply here. The model carries five flows and their stated protocols are HTTPS, gRPC and the PostgreSQL wire protocol; no element's technology names an XML parser, a SOAP endpoint or an XSLT processor, and no data description names an XML document. The requirement has no subject in this system, so it is ruled out on the protocols the flows state rather than on silence.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "The web API hands each transfer to the ledger service over gRPC",
      "source_label": "Payments platform notes"
    }
  ]
}
```
