# Elevation of Privilege Exemplars

Three drafts against the exemplar system, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: a confused deputy holding standing database authority

`process:ledger-service` accepts instructions over `flow:web-api-to-ledger-service:post-transfer` with `authentication: none`, and holds a full read/write credential to `store:accounts-db`. It makes no authorization decision of its own about whose money is being moved, yet it carries authority over everyone's.

Lane contrast: being accepted *as* the web API is spoofing. Directing an honest, over-privileged service to use authority the caller does not have is yours.

```json
{
  "sequence": 1,
  "title": "The ledger service lends its full database authority to any caller",
  "description": "`process:ledger-service` performs transfers using the standing full read/write credential on `flow:ledger-service-to-accounts-db:read-write-balances`, and takes its instructions from `flow:web-api-to-ledger-service:post-transfer`, which carries `authentication: none`. Nothing binds an instruction to an authorized customer at the point authority is exercised: the caller supplies the account identifiers and the ledger acts on them with its own privilege, the classic confused deputy. An attacker who can reach the ledger moves funds between arbitrary accounts — horizontal escalation across the entire customer base — without ever holding a customer credential. Second-order: the same standing authority covers all rows of `store:accounts-db`, so any code-execution flaw in this process inherits it wholesale.",
  "affected_element_ids": [
    "process:ledger-service",
    "flow:web-api-to-ledger-service:post-transfer",
    "flow:ledger-service-to-accounts-db:read-write-balances",
    "store:accounts-db"
  ],
  "verb": "abuse-grant",
  "evidence_refs": [],
  "quotes": [
    {
      "text": "that account has full read/write on every table",
      "source_label": "Payments platform notes"
    }
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium: exercising the deputy needs a position from which the unauthenticated flow is reachable, but no credential. Impact is high: authority spans every account in `store:accounts-db`, tagged `financial` and `pii`."
  },
  "mitigations": [
    {
      "summary": "Enforce object-level authorization at the ledger",
      "detail": "Require `process:ledger-service` to verify that the authenticated principal owns the source account, using an identity propagated from `flow:customer-to-web-api:submit-payment` rather than caller-supplied identifiers."
    },
    {
      "summary": "Reduce the standing grant",
      "detail": "Replace the full read/write credential with narrowly scoped, short-lived database authority for the transfer operation."
    }
  ]
}
```

## Second-order, in system B: a tenant boundary the attacker fills in

Written against exemplar system B. Escalation does not need a privilege bug when the system asks the caller which privileges to apply. Here the tenant — the only thing separating one customer's data from another's — is a field in untrusted input, and the identity that acts on it holds authority over all of them.

```json
{
  "sequence": 2,
  "title": "A publisher selects its own tenant and writes into any customer's partition",
  "description": "`process:stream-processor` reads the tenant key out of the device payload carried on `flow:sensor-gateway-to-mqtt-broker:publish-telemetry`, which crosses from `boundary:field` into `boundary:ingest`, and then writes under that key over `flow:stream-processor-to-telemetry-store:write-readings` using a service account holding write on every tenant partition. Nothing between the two re-derives the tenant from an authenticated identity, so a publisher that sets another customer's tenant in its own payload is not defeating an authorization check — it is supplying the input that check would have been made from. Second-order: the writing identity already spans the whole of `store:telemetry-store`, so the escalation is from one fleet's data to every tenant's in a single hop, and the readings land through the ordinary ingest path rather than an anomalous one.",
  "affected_element_ids": [
    "entity:sensor-gateway",
    "process:stream-processor",
    "flow:sensor-gateway-to-mqtt-broker:publish-telemetry",
    "flow:stream-processor-to-telemetry-store:write-readings",
    "store:telemetry-store"
  ],
  "verb": "escalate",
  "evidence_refs": [
    "crossing:flow:sensor-gateway-to-mqtt-broker:publish-telemetry"
  ],
  "quotes": [
    {
      "text": "takes the tenant_id straight out of the device payload",
      "source_label": "Fleet telemetry platform notes"
    },
    {
      "text": "Its service account can write every tenant's partition.",
      "source_label": "Fleet telemetry platform notes"
    }
  ],
  "severity": {
    "likelihood": "high",
    "impact": "high",
    "justification": "Likelihood is high: the tenant arrives on a derived crossing into `boundary:ingest` and the attacker needs only to set a field, with no flaw to find in `process:stream-processor`. Impact is high: the write reaches `store:telemetry-store`, classified confidential and tagged `business-critical-data`, for tenants the publisher has no relationship with."
  },
  "mitigations": [
    {
      "summary": "Take the tenant from the authenticated publisher",
      "detail": "Resolve the tenant in `process:stream-processor` from the credential that authenticated the publish, and reject a payload whose declared tenant disagrees with it."
    },
    {
      "summary": "Scope the write identity to one tenant at a time",
      "detail": "Replace the platform-wide service account on `flow:stream-processor-to-telemetry-store:write-readings` with a per-tenant credential, so a wrong tenant key fails the write instead of performing it."
    }
  ]
}
```

## Unknown-conditional: unverified reachability of the ledger service

`process:ledger-service` carries `exposure: unknown`. Condition the escalation on that attribute and let the critic mark it needs-info; the model does not say the process is reachable from outside `boundary:core`.

```json
{
  "sequence": 3,
  "title": "Direct transfer authority if the ledger service is reachable beyond the core zone",
  "description": "`process:ledger-service` has `exposure: unknown`, and it accepts transfer instructions over `flow:web-api-to-ledger-service:post-transfer` with `authentication: none` while holding full authority over `store:accounts-db`. If that unknown resolves to reachability beyond `boundary:core` — a load balancer, a peered network, a management interface — the attacker population for that unauthenticated surface is no longer limited to holders of a dmz foothold, and the escalation in E-01 becomes directly available without any prior compromise. This draft is conditional on the `exposure` attribute of `process:ledger-service`; it is not a claim that the process is externally reachable.",
  "affected_element_ids": [
    "process:ledger-service",
    "flow:web-api-to-ledger-service:post-transfer",
    "store:accounts-db"
  ],
  "verb": "escalate",
  "evidence_refs": [
    "unknown:process:ledger-service:exposure"
  ],
  "quotes": [],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium and conditional on the unknown `exposure` value: if reachable, exploitation needs no credential and no foothold, but the process may in fact be core-only. Impact is high: unmediated transfer authority over `store:accounts-db`, tagged `financial` and `pii`."
  },
  "mitigations": [
    {
      "summary": "Establish and then constrain the ledger's reachability",
      "detail": "Determine what can route to `process:ledger-service`; restrict ingress to `process:web-api` by network policy and authenticate the caller regardless, so reachability alone never grants authority."
    }
  ]
}
```
