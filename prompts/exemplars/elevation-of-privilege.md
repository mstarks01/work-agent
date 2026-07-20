# Elevation of Privilege Exemplars

Three drafts against the exemplar system, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: a confused deputy holding standing database authority

`process:ledger-service` accepts instructions over `flow:web-api-to-ledger-service:post-transfer` with `authentication: none`, and holds a full read/write credential to `store:accounts-db`. It makes no authorization decision of its own about whose money is being moved, yet it carries authority over everyone's.

Lane contrast: being accepted *as* the web API is spoofing. Directing an honest, over-privileged service to use authority the caller does not have is yours.

```json
{
  "id": "E-01",
  "category": "elevation-of-privilege",
  "title": "The ledger service lends its full database authority to any caller",
  "description": "`process:ledger-service` performs transfers using the standing full read/write credential on `flow:ledger-service-to-accounts-db:read-write-balances`, and takes its instructions from `flow:web-api-to-ledger-service:post-transfer`, which carries `authentication: none`. Nothing binds an instruction to an authorized customer at the point authority is exercised: the caller supplies the account identifiers and the ledger acts on them with its own privilege, the classic confused deputy. An attacker who can reach the ledger moves funds between arbitrary accounts — horizontal escalation across the entire customer base — without ever holding a customer credential. Second-order: the same standing authority covers all rows of `store:accounts-db`, so any code-execution flaw in this process inherits it wholesale.",
  "affected_element_ids": [
    "process:ledger-service",
    "flow:web-api-to-ledger-service:post-transfer",
    "flow:ledger-service-to-accounts-db:read-write-balances",
    "store:accounts-db"
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

## Second-order: zone escape from the dmz into the core

The exemplar is reach across a trust boundary. `process:web-api` is `internet-facing` and holds nothing valuable itself; its worth to an attacker is the path it opens. Score impact on the zone entered, not on the element compromised.

```json
{
  "id": "E-02",
  "category": "elevation-of-privilege",
  "title": "A compromised web API escalates into core-zone transfer authority",
  "description": "`process:web-api` is `internet-facing` in `boundary:dmz` and holds no `financial` assets of its own. But it is the origin of `flow:web-api-to-ledger-service:post-transfer`, which crosses into `boundary:core` and is accepted with `authentication: none`. Any flaw yielding code execution or request forgery in the web tier therefore grants the attacker whatever `process:ledger-service` will do — and that process exercises full read/write authority over `store:accounts-db`. Second-order: the escalation chain converts an edge foothold into standing authority in the highest-trust zone, and because the transfers are made through the legitimate ledger path they appear in `store:audit-log` as ordinary activity.",
  "affected_element_ids": [
    "process:web-api",
    "process:ledger-service",
    "flow:web-api-to-ledger-service:post-transfer",
    "store:accounts-db"
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium: the entry point faces the internet, but the attacker needs an initial flaw in `process:web-api` before the escalation is available. Impact is high: the attacker crosses a derived boundary crossing into `boundary:core` and reaches `financial` and `pii` data across all customers."
  },
  "mitigations": [
    {
      "summary": "Authorize the caller at the zone boundary",
      "detail": "Require a verified workload identity plus per-request customer authorization on `flow:web-api-to-ledger-service:post-transfer`, so dmz code execution does not equal core authority."
    },
    {
      "summary": "Constrain what the ledger will do for the web tier",
      "detail": "Limit the operations, amounts, and accounts the web-API identity may request, so a compromised edge cannot exercise unbounded transfer authority."
    }
  ]
}
```

## Unknown-conditional: unverified reachability of the ledger service

`process:ledger-service` carries `exposure: unknown`. Condition the escalation on that attribute and let the critic mark it needs-info; the model does not say the process is reachable from outside `boundary:core`.

```json
{
  "id": "E-03",
  "category": "elevation-of-privilege",
  "title": "Direct transfer authority if the ledger service is reachable beyond the core zone",
  "description": "`process:ledger-service` has `exposure: unknown`, and it accepts transfer instructions over `flow:web-api-to-ledger-service:post-transfer` with `authentication: none` while holding full authority over `store:accounts-db`. If that unknown resolves to reachability beyond `boundary:core` — a load balancer, a peered network, a management interface — the attacker population for that unauthenticated surface is no longer limited to holders of a dmz foothold, and the escalation in E-01 becomes directly available without any prior compromise. This draft is conditional on the `exposure` attribute of `process:ledger-service`; it is not a claim that the process is externally reachable.",
  "affected_element_ids": [
    "process:ledger-service",
    "flow:web-api-to-ledger-service:post-transfer",
    "store:accounts-db"
  ],
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
