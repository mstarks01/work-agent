# Denial of Service Exemplars

Three drafts against the exemplar system, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: asymmetric work on an internet-facing endpoint

`process:web-api` is `internet-facing` in `boundary:dmz`, and every request over `flow:customer-to-web-api:submit-payment` triggers a synchronous gRPC call and a database write. Cheap for the attacker, expensive for the system: the definition of an asymmetric path.

The model states no rate limiting. Silence is not a control — rate likelihood assuming none, and say so — but do not write "rate limiting is missing" as though the model asserted it.

```json
{
  "sequence": 1,
  "title": "Request flooding on the payment endpoint exhausts the web API",
  "description": "`process:web-api` is `internet-facing`, so the attacker population is the whole internet, and each request on `flow:customer-to-web-api:submit-payment` costs a TLS session, a synchronous gRPC call to `process:ledger-service`, and a write through to `store:accounts-db`. An attacker with commodity tooling saturates the endpoint at a fraction of that cost, and no compensating control appears in the model. Second-order: the load does not stop at the dmz — it is propagated across `flow:web-api-to-ledger-service:post-transfer` into `boundary:core`, so a flood aimed at the public surface degrades a component tagged `availability-critical`, and legitimate payments fail while the attack runs.",
  "affected_element_ids": [
    "process:web-api",
    "flow:customer-to-web-api:submit-payment",
    "process:ledger-service"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment"
  ],
  "quotes": [
    {
      "text": "the only thing we expose to the internet",
      "source_label": "Payments platform notes"
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
  "sequence": 2,
  "title": "Database connection exhaustion cascades into total transfer outage",
  "description": "Every payment path terminates at `store:accounts-db` over `flow:ledger-service-to-accounts-db:read-write-balances`, whose PostgreSQL connections and throughput are a finite shared resource. Sustained traffic through `process:web-api`, or slow-running queries induced by expensive request shapes, exhausts that capacity. Second-order: `process:ledger-service` (`availability-critical`) cannot complete or roll back transfers once the pool is starved, `flow:ledger-service-to-audit-log:append-transfer-record` stops producing records so the outage window is also an accountability gap, and the failure surfaces to `entity:customer` as declined payments even though nothing in the dmz is under direct attack.",
  "affected_element_ids": [
    "store:accounts-db",
    "process:ledger-service",
    "flow:ledger-service-to-accounts-db:read-write-balances",
    "store:audit-log"
  ],
  "evidence_refs": [],
  "quotes": [
    {
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

## Unknown-conditional, in system B: unverified reachability of the processor

Written against exemplar system B. The trigger is an `unknown` on an element rather than a flow: `process:stream-processor` carries `exposure: unknown`, so where the load can be applied is exactly what the model does not record. Condition the threat on that attribute; the model does not say the processor is reachable.

```json
{
  "sequence": 3,
  "title": "The stream processor may be floodable without passing the broker",
  "description": "`process:stream-processor` carries `exposure: unknown`. The design intent is that work reaches it only over `flow:mqtt-broker-to-stream-processor:consume-topic`, so `process:mqtt-broker` is where backpressure, quotas and disconnection would be applied. If that unknown resolves to reachability beyond `boundary:platform`, an attacker submits work directly to the processor and none of the broker's metering is in the path: the queue that is supposed to absorb a burst is bypassed rather than filled. Second-order: the processor is the sole writer on `flow:stream-processor-to-telemetry-store:write-readings`, so saturating it stalls ingest for every tenant at once and genuine readings from `entity:sensor-gateway` are delayed or dropped platform-wide, not for the fleet that was targeted. This draft is conditional on that element's `exposure` attribute; it is not a claim that the processor is exposed.",
  "affected_element_ids": [
    "process:stream-processor",
    "process:mqtt-broker",
    "flow:mqtt-broker-to-stream-processor:consume-topic",
    "store:telemetry-store"
  ],
  "evidence_refs": [
    "unknown:process:stream-processor:exposure",
    "crossing:flow:mqtt-broker-to-stream-processor:consume-topic"
  ],
  "quotes": [],
  "severity": {
    "likelihood": "low",
    "impact": "medium",
    "justification": "Likelihood is low and conditional on the unknown `exposure` value: the stated design puts `process:stream-processor` inside `boundary:platform` behind the broker, and the attack needs that placement to be wrong. Impact is medium: ingest stalls for every tenant and `store:telemetry-store` falls behind, but the outage is recoverable and no data is disclosed."
  },
  "mitigations": [
    {
      "summary": "Record where the processor is reachable from, then close it",
      "detail": "Establish the network exposure of `process:stream-processor`; if it accepts work from outside `boundary:platform`, restrict it to the broker's subscription path."
    },
    {
      "summary": "Meter at the consumer as well as the broker",
      "detail": "Apply per-tenant concurrency and rate bounds inside `process:stream-processor`, so backpressure does not depend on every producer arriving through `process:mqtt-broker`."
    }
  ]
}
```
