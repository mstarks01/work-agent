# Denial of Service Exemplars

Three drafts against the exemplar system, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: asymmetric work on an internet-facing endpoint

`process:web-api` is `internet-facing` in `boundary:dmz`, and every request over `flow:customer-to-web-api:submit-payment` triggers a synchronous gRPC call and a database write. Cheap for the attacker, expensive for the system: the definition of an asymmetric path.

The model states no rate limiting. Silence is not a control — rate likelihood assuming none, and say so — but do not write "rate limiting is missing" as though the model asserted it.

```json
{
  "id": "D-01",
  "category": "denial-of-service",
  "title": "Request flooding on the payment endpoint exhausts the web API",
  "description": "`process:web-api` is `internet-facing`, so the attacker population is the whole internet, and each request on `flow:customer-to-web-api:submit-payment` costs a TLS session, a synchronous gRPC call to `process:ledger-service`, and a write through to `store:accounts-db`. An attacker with commodity tooling saturates the endpoint at a fraction of that cost, and no compensating control appears in the model. Second-order: the load does not stop at the dmz — it is propagated across `flow:web-api-to-ledger-service:post-transfer` into `boundary:core`, so a flood aimed at the public surface degrades a component tagged `availability-critical`, and legitimate payments fail while the attack runs.",
  "affected_element_ids": [
    "process:web-api",
    "flow:customer-to-web-api:submit-payment",
    "process:ledger-service"
  ],
  "grounds": [
    {
      "kind": "quote",
      "text": "the only thing we expose to the internet",
      "source_label": "Payments platform notes"
    },
    {
      "kind": "derived-fact",
      "flow_id": "flow:customer-to-web-api:submit-payment"
    }
  ],
  "severity": {
    "likelihood": "high",
    "impact": "medium",
    "justification": "Likelihood is high: `exposure: internet-facing` on a derived crossing from `boundary:public-internet`, exploitable with public tooling and no prerequisites. Impact is medium: payments are unavailable while the flood runs and `reputation` is exposed, but the outage is recoverable and no data is lost."
  },
  "mitigations": [
    {
      "summary": "Rate-limit and shed load at the edge",
      "detail": "Apply per-source and per-account rate limits plus request-size caps in front of `process:web-api`, and return early rather than propagating load to `process:ledger-service`."
    },
    {
      "summary": "Decouple the synchronous call chain",
      "detail": "Queue accepted instructions so a burst at `process:web-api` cannot translate one-to-one into ledger and database work."
    }
  ]
}
```

## Second-order: one exhausted dependency takes down the core

The exemplar is the cascade. `store:accounts-db` is a shared dependency: its connection capacity is finite, and everything in `boundary:core` sits behind it. Score impact on the reachable set — the store's own degradation is the smaller half of the harm.

```json
{
  "id": "D-02",
  "category": "denial-of-service",
  "title": "Database connection exhaustion cascades into total transfer outage",
  "description": "Every payment path terminates at `store:accounts-db` over `flow:ledger-service-to-accounts-db:read-write-balances`, whose PostgreSQL connections and throughput are a finite shared resource. Sustained traffic through `process:web-api`, or slow-running queries induced by expensive request shapes, exhausts that capacity. Second-order: `process:ledger-service` (`availability-critical`) cannot complete or roll back transfers once the pool is starved, `flow:ledger-service-to-audit-log:append-transfer-record` stops producing records so the outage window is also an accountability gap, and the failure surfaces to `entity:customer` as declined payments even though nothing in the dmz is under direct attack.",
  "affected_element_ids": [
    "store:accounts-db",
    "process:ledger-service",
    "flow:ledger-service-to-accounts-db:read-write-balances",
    "store:audit-log"
  ],
  "grounds": [
    {
      "kind": "quote",
      "text": "a small fixed connection pool, so a slow query anywhere backs everything up",
      "source_label": "Payments platform notes"
    }
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium: reaching the database requires driving load through `process:web-api` rather than attacking the store directly. Impact is high: `process:ledger-service` is tagged `availability-critical`, the whole transfer function stops, and audit records are lost for the duration."
  },
  "mitigations": [
    {
      "summary": "Bound and partition database capacity",
      "detail": "Set per-service connection-pool ceilings and statement timeouts on `store:accounts-db` so one caller cannot consume the whole pool."
    },
    {
      "summary": "Fail the ledger open on evidence, not on money",
      "detail": "Buffer audit appends durably so an outage of `store:accounts-db` does not also erase the record of what was attempted."
    }
  ]
}
```

## Unknown-conditional: unauthenticated amplification through the webhook

`flow:payments-provider-to-web-api:settlement-webhook` carries `authentication: unknown`. Condition the threat on that attribute and let the critic mark it needs-info; the model does not say the endpoint is open.

```json
{
  "id": "D-03",
  "category": "denial-of-service",
  "title": "Settlement webhook may be an unauthenticated work amplifier",
  "description": "`flow:payments-provider-to-web-api:settlement-webhook` reaches `process:web-api` from `boundary:public-internet` with `authentication: unknown`. If that unknown resolves to no verification, the endpoint does real settlement work — ledger calls and database writes — for any caller who knows the URL, with no credential to revoke and no account to rate-limit against. An attacker replays or fabricates deliveries at volume, and the cost lands in `boundary:core` rather than at the edge. Second-order: the amplified load reaches `process:ledger-service` and `store:accounts-db`, the same shared dependency the transfer path needs. This draft is conditional on the `authentication` attribute of that flow.",
  "affected_element_ids": [
    "process:web-api",
    "flow:payments-provider-to-web-api:settlement-webhook",
    "process:ledger-service"
  ],
  "grounds": [
    {
      "kind": "derived-fact",
      "flow_id": "flow:payments-provider-to-web-api:settlement-webhook"
    },
    {
      "kind": "unknown-attribute",
      "element_id": "flow:payments-provider-to-web-api:settlement-webhook",
      "attribute": "authentication"
    }
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "medium",
    "justification": "Likelihood is medium and conditional on the unknown `authentication` value: the endpoint is internet-reachable and the attack needs no credential if unverified, but the control may in fact be present. Impact is medium: an availability outage of the payment path, recoverable, with no data loss."
  },
  "mitigations": [
    {
      "summary": "Establish webhook authentication, then meter per consumer",
      "detail": "Determine what verifies this callback; require per-consumer signatures, and apply rate limits and replay rejection keyed to the verified sender."
    }
  ]
}
```
