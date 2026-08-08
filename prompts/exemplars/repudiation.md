# Repudiation Exemplars

Three drafts against the exemplar system, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: audit records that cannot name the customer

`flow:ledger-service-to-audit-log:append-transfer-record` writes records tagged with the ledger service identity only. The system logs — but the evidence names the wrong actor, so a disputed transfer cannot be pinned to the person who initiated it.

The lane test: the harm here is not that someone impersonated a customer (spoofing) and not that a record was altered (tampering). It is that the record, written correctly and never touched, proves nothing about who acted.

```json
{
  "id": "R-01",
  "category": "repudiation",
  "title": "A customer can deny a transfer because audit records name only the ledger service",
  "description": "Transfers initiated by `entity:customer` over `flow:customer-to-web-api:submit-payment` are recorded by `process:ledger-service` through `flow:ledger-service-to-audit-log:append-transfer-record`, whose entries are tagged with the ledger service identity only. No record in `store:audit-log` binds the transfer to the initiating customer, and the session cookie on the inbound flow is not carried through to the log entry. A customer disputing a transfer — genuinely defrauded or acting in bad faith — cannot be contradicted by evidence, and the operator cannot distinguish the two cases. Second-order: chargeback and fraud handling has no factual basis, so `financial` losses are absorbed by the operator and `entity:customer` records are never conclusive.",
  "affected_element_ids": [
    "entity:customer",
    "process:ledger-service",
    "store:audit-log",
    "flow:ledger-service-to-audit-log:append-transfer-record"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment"
  ],
  "quotes": [
    {
      "text": "the entry names the ledger service and never the customer",
      "source_label": "Payments platform notes"
    }
  ],
  "severity": {
    "likelihood": "high",
    "impact": "medium",
    "justification": "Likelihood is high: the gap is structural — every transfer is recorded this way, and disputes need no attacker at all. Impact is medium: `financial` loss per dispute is bounded, but `store:audit-log` (`business-critical-data`) cannot resolve any of them."
  },
  "mitigations": [
    {
      "summary": "Bind the initiating identity into every transfer record",
      "detail": "Propagate the authenticated customer identity from `flow:customer-to-web-api:submit-payment` through `process:web-api` and `process:ledger-service` into the audit entry, alongside timestamp, source address, and outcome."
    },
    {
      "summary": "Strengthen the identity the record can cite",
      "detail": "Evidence is only as strong as the authentication behind it; a password-only session limits what any record can prove about the actor."
    }
  ]
}
```

## Second-order: the ledger writes its own evidence

`process:ledger-service` both performs transfers and produces the only account of them, and it accepts instructions over an unauthenticated flow. The exemplar shows the reach: compromising one modest process does not lose one record, it makes the entire corpus of accountability worthless.

```json
{
  "id": "R-02",
  "category": "repudiation",
  "title": "Compromising the ledger service destroys accountability for every transfer",
  "description": "`process:ledger-service` is the sole writer to `store:audit-log` over `flow:ledger-service-to-audit-log:append-transfer-record`, using its own service account, and it accepts work over `flow:web-api-to-ledger-service:post-transfer` with `authentication: none`. An attacker who reaches `boundary:dmz` and drives, or ultimately compromises, that process controls both the action and the record of it: entries can be omitted for the transfers the attacker makes, and fabricated for transfers that never happened. Second-order: because no independent party writes to `store:audit-log`, every prior record becomes unreliable once this identity is suspected — the accountability loss is retrospective across the whole corpus, not limited to the incident window.",
  "affected_element_ids": [
    "process:ledger-service",
    "store:audit-log",
    "flow:ledger-service-to-audit-log:append-transfer-record",
    "flow:web-api-to-ledger-service:post-transfer"
  ],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "the entry names the ledger service and never the customer",
      "source_label": "Payments platform notes"
    },
    {
      "text": "not authenticated and not encrypted",
      "source_label": "Payments platform notes"
    }
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium: driving the ledger needs only a foothold in `boundary:dmz` given `authentication: none` on the inbound flow. Impact is high: `store:audit-log` is tagged `business-critical-data` and is the only evidence of `financial` actions, so the loss covers all history rather than one event."
  },
  "mitigations": [
    {
      "summary": "Make audit entries append-only to their writer",
      "detail": "Grant `process:ledger-service` append-only permission with retention lock on `store:audit-log`, so the acting party cannot omit or rewrite entries after the fact."
    },
    {
      "summary": "Record transfers from a second, independent point",
      "detail": "Have `process:web-api` log the accepted instruction independently, so two records must be corrupted rather than one."
    }
  ]
}
```

## Unknown-conditional: undisputable settlement callbacks

`flow:payments-provider-to-web-api:settlement-webhook` carries `authentication: unknown`. Write the evidence gap conditionally on that attribute and let the critic mark it needs-info — the model does not say the callback is unverified, only that nobody recorded what verifies it.

```json
{
  "id": "R-03",
  "category": "repudiation",
  "title": "Settlement confirmations may be unattributable to the payments provider",
  "description": "`flow:payments-provider-to-web-api:settlement-webhook` crosses from `boundary:public-internet` into `boundary:dmz` with `authentication: unknown`. If that unknown resolves to an unsigned callback, nothing in the received settlement confirmation ties it to `entity:payments-provider`: the operator cannot prove to the provider that a given settlement was sent, and the provider can deny having sent one that was acted on. Disputes over money already moved through `process:ledger-service` would have no evidence on either side. This draft is conditional on the `authentication` attribute of that flow; it is not a claim that the callback is unsigned.",
  "affected_element_ids": [
    "entity:payments-provider",
    "process:web-api",
    "flow:payments-provider-to-web-api:settlement-webhook"
  ],
  "evidence_refs": [
    "crossing:flow:payments-provider-to-web-api:settlement-webhook",
    "unknown:flow:payments-provider-to-web-api:settlement-webhook:authentication"
  ],
  "quotes": [],
  "severity": {
    "likelihood": "medium",
    "impact": "medium",
    "justification": "Likelihood is medium and conditional on the unknown `authentication` value: partner disputes are routine and need no attacker, but the control may in fact be present. Impact is medium: individual settlement disputes carry `financial` and `reputation` cost, bounded per event."
  },
  "mitigations": [
    {
      "summary": "Retain signed callbacks as evidence",
      "detail": "Establish what authenticates this webhook; if nothing does, require per-consumer signatures and store the raw signed payload so a settlement can be proved after the fact."
    }
  ]
}
```
