# Spoofing Exemplars

Three drafts against the exemplar system, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: password-only customer identity

The trigger is stated outright: `flow:customer-to-web-api:submit-payment` authenticates with a session cookie issued after a password login and no MFA, and the flow crosses from `boundary:public-internet` into `boundary:dmz`. The attacker population is everyone on the internet, and the prerequisite — a breached password or a phishing proxy — is cheap.

Note the phrasing. "The login flow lacks MFA" is a control observation and would be rejected; the threat is the attacker action that the missing control permits, with the impersonated identity, the target, and the flow ID all named.

```json
{
  "sequence": 1,
  "title": "Credential stuffing lets an attacker transact as any customer",
  "description": "An attacker on the public internet replays credentials from breach corpora, or runs a real-time phishing proxy, against the login backing `flow:customer-to-web-api:submit-payment`, which authenticates `entity:customer` with a password and a session cookie and no second factor. A successful login yields a session indistinguishable from the genuine customer's, so the attacker submits payment instructions on that customer's behalf through `process:web-api`. Second-order: the accepted instruction is written through to `store:accounts-db`, so the impersonation reaches the customer's balances and account-holder PII, not just the web tier.",
  "affected_element_ids": [
    "entity:customer",
    "process:web-api",
    "flow:customer-to-web-api:submit-payment",
    "store:accounts-db"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment"
  ],
  "quotes": [
    {
      "text": "sign in with an email and password and get a session cookie; we never added MFA",
      "source_label": "Payments platform notes"
    }
  ],
  "severity": {
    "likelihood": "high",
    "impact": "high",
    "justification": "Likelihood is high: the flow is a derived crossing from `boundary:public-internet`, `process:web-api` is `internet-facing`, and single-factor password authentication is defeated by public tooling. Impact is high: the session moves money and reaches `store:accounts-db`, tagged `pii` and `financial` and classified confidential."
  },
  "mitigations": [
    {
      "summary": "Require phishing-resistant MFA for customer authentication",
      "detail": "Move `flow:customer-to-web-api:submit-payment` to WebAuthn/passkey authentication, so possession of a password no longer yields a session."
    },
    {
      "summary": "Screen credentials and rate-limit authentication",
      "detail": "Reject breached passwords at set and login time, and rate-limit per-account and per-source attempts against `process:web-api`."
    }
  ]
}
```

## Second-order, in system B: one certificate speaks for every fleet

Written against exemplar system B, to show the same reasoning on an event-driven system. A shared credential is not one identity weakly held — it is every identity held by whoever extracts it once. The reach is what makes the draft: the credential's blast radius is the whole tenant population, not the device it came from.

```json
{
  "sequence": 2,
  "title": "One extracted device certificate lets an attacker publish as any fleet",
  "description": "`flow:sensor-gateway-to-mqtt-broker:publish-telemetry` authenticates every `entity:sensor-gateway` with the same client certificate, burned into a device image an attacker can buy, dump, or pull from an update feed. `process:mqtt-broker` is `internet-facing`, so one extraction yields the ability to publish from anywhere as an apparently genuine gateway, and the certificate identifies the fleet software rather than a device. Second-order: the payload the impostor publishes names its own tenant, so `process:stream-processor` files the fabricated readings under whichever tenant the attacker chooses in `store:telemetry-store`, and the reach is every customer fleet on the platform rather than the one device that leaked. Revocation has the same shape: the certificate cannot be withdrawn from the impostor without cutting off every genuine gateway with it.",
  "affected_element_ids": [
    "entity:sensor-gateway",
    "process:mqtt-broker",
    "flow:sensor-gateway-to-mqtt-broker:publish-telemetry",
    "process:stream-processor",
    "store:telemetry-store"
  ],
  "evidence_refs": [
    "crossing:flow:sensor-gateway-to-mqtt-broker:publish-telemetry"
  ],
  "quotes": [
    {
      "text": "they all share one client certificate",
      "source_label": "Fleet telemetry platform notes"
    }
  ],
  "severity": {
    "likelihood": "high",
    "impact": "high",
    "justification": "Likelihood is high: the flow is a derived crossing into `boundary:ingest`, `process:mqtt-broker` is `internet-facing`, and extracting a static certificate from a device image needs no access to the platform at all. Impact is high: the impersonation reaches `store:telemetry-store`, classified confidential and tagged `business-critical-data`, across every tenant rather than one."
  },
  "mitigations": [
    {
      "summary": "Give each device its own credential",
      "detail": "Issue a per-device certificate at provisioning and bind it to the device's tenant on `flow:sensor-gateway-to-mqtt-broker:publish-telemetry`, so a compromised gateway is revocable alone and can speak only for its own fleet."
    },
    {
      "summary": "Derive the tenant from the credential, not the payload",
      "detail": "Have `process:stream-processor` take the tenant from the authenticated identity of the publisher, so a forged payload cannot select a tenant."
    }
  ]
}
```

## Unknown-conditional: unverified settlement webhook

`flow:payments-provider-to-web-api:settlement-webhook` has `authentication: unknown`. The model does not say the webhook is unauthenticated — it says nobody recorded whether it is. Write the threat conditionally and name the attribute; the critic will mark it needs-info. Writing "the webhook is unauthenticated" would assert a fact the model does not contain.

```json
{
  "sequence": 3,
  "title": "Forged settlement callbacks if the webhook's authentication is absent",
  "description": "`flow:payments-provider-to-web-api:settlement-webhook` crosses from `boundary:public-internet` into `boundary:dmz` with `authentication: unknown`. If that unknown resolves to no verification — no signature, no mutual TLS — anyone who learns the endpoint URL can impersonate `entity:payments-provider` and post fabricated settlement confirmations to `process:web-api`. Second-order: the ledger acts on those confirmations, so forged settlements become balance changes in `store:accounts-db`. This draft is conditional on the `authentication` attribute of that flow; it is not a claim that the control is missing.",
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
    "impact": "high",
    "justification": "Likelihood is medium and conditional on the unknown `authentication` value: the endpoint is reachable from `boundary:public-internet`, but the control may in fact be present. Impact is high: fabricated settlements alter `financial` state in `store:accounts-db` via `process:ledger-service`."
  },
  "mitigations": [
    {
      "summary": "Record and then enforce webhook authentication",
      "detail": "Establish what verifies this callback; if nothing does, require per-consumer HMAC signatures with timestamp and nonce, and reject stale or replayed deliveries."
    }
  ]
}
```
