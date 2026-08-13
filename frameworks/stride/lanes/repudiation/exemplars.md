# Repudiation Exemplars

Three drafts against the exemplar system, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: audit records that cannot name the customer

`flow:ledger-service-to-audit-log:append-transfer-record` writes records tagged with the ledger service identity only. The system logs — but the evidence names the wrong actor, so a disputed transfer cannot be pinned to the person who initiated it.

The lane test: the harm here is not that someone impersonated a customer (spoofing) and not that a record was altered (tampering). It is that the record, written correctly and never touched, proves nothing about who acted.

```json
{
  "sequence": 1,
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

## Second-order, in system B: readings nobody can attribute to a device

Written against exemplar system B. Repudiation does not require anyone to tamper with a record — it is enough that the record was never capable of naming who acted. Here two facts compose: the credential identifies no single device, and the only tenant marking is one the publisher supplied.

```json
{
  "sequence": 2,
  "title": "No stored reading can be tied to the device that produced it",
  "description": "Every `entity:sensor-gateway` authenticates on `flow:sensor-gateway-to-mqtt-broker:publish-telemetry` with the same certificate, so the credential distinguishes the fleet software and not the device. `process:stream-processor` then files each reading in `store:telemetry-store` under a tenant the payload itself declared. Both halves of an attributable record are therefore missing: the transport identifies a population rather than a party, and the only party marking present is self-asserted by whoever published. A customer disputing a reading — a reported fault, a billed volume, an SLA breach — can say it was not their device, and the platform holds nothing that contradicts them. Second-order: this is not one unattributable event but a property of the whole store, so the first genuine dispute puts every historical reading for every tenant beyond defence, and an attacker publishing forged readings inherits that deniability rather than having to create it.",
  "affected_element_ids": [
    "entity:sensor-gateway",
    "process:stream-processor",
    "flow:sensor-gateway-to-mqtt-broker:publish-telemetry",
    "store:telemetry-store"
  ],
  "evidence_refs": [
    "crossing:flow:sensor-gateway-to-mqtt-broker:publish-telemetry"
  ],
  "quotes": [
    {
      "text": "they all share one client certificate",
      "source_label": "Fleet telemetry platform notes"
    },
    {
      "text": "takes the tenant_id straight out of the device payload",
      "source_label": "Fleet telemetry platform notes"
    }
  ],
  "severity": {
    "likelihood": "high",
    "impact": "medium",
    "justification": "Likelihood is high: no attacker is needed for the gap to bite — an ordinary billing or fault dispute reaches it, and the publish flow is a derived crossing out of `boundary:field` that any holder of the shared certificate can originate. Impact is medium: `store:telemetry-store` is tagged `business-critical-data` and its evidential value is lost across all tenants, but the readings themselves remain available and no `pii` is exposed by this threat alone."
  },
  "mitigations": [
    {
      "summary": "Identify the device, not the fleet",
      "detail": "Issue per-device credentials on `flow:sensor-gateway-to-mqtt-broker:publish-telemetry` and record the authenticated device identity with each reading, so a stored row names a party."
    },
    {
      "summary": "Stop trusting the payload for attribution",
      "detail": "Have `process:stream-processor` derive the tenant from the authenticated publisher and reject a self-declared one, so the marking in `store:telemetry-store` is the platform's claim rather than the publisher's."
    }
  ]
}
```

## Unknown-conditional: undisputable settlement callbacks

`flow:payments-provider-to-web-api:settlement-webhook` carries `authentication: unknown`. Write the evidence gap conditionally on that attribute and let the critic mark it needs-info — the model does not say the callback is unverified, only that nobody recorded what verifies it.

```json
{
  "sequence": 3,
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
