# Spoofing Exemplars

Three drafts against the exemplar system, showing the shape and the reasoning. Follow the reasoning, not the wording.

## Canonical: password-only customer identity

The trigger is stated outright: `flow:customer-to-web-api:submit-payment` authenticates with a session cookie issued after a password login and no MFA, and the flow crosses from `boundary:public-internet` into `boundary:dmz`. The attacker population is everyone on the internet, and the prerequisite — a breached password or a phishing proxy — is cheap.

Note the phrasing. "The login flow lacks MFA" is a control observation and would be rejected; the threat is the attacker action that the missing control permits, with the impersonated identity, the target, and the flow ID all named.

```json
{
  "id": "S-01",
  "category": "spoofing",
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

## Second-order: impersonating the web API to the ledger

`flow:web-api-to-ledger-service:post-transfer` has `authentication: none` — identity is inferred from network position. On its own that is a modest finding about an internal call. The value of the draft is the reach: any foothold in `boundary:dmz` inherits the ability to speak as `process:web-api`, and impact is scored on everything that identity commands, not on the compromised element.

```json
{
  "id": "S-02",
  "category": "spoofing",
  "title": "Any dmz foothold can impersonate the web API to the ledger service",
  "description": "`flow:web-api-to-ledger-service:post-transfer` carries `authentication: none`; `process:ledger-service` accepts transfer instructions because they arrive from `boundary:dmz`, not because the caller proved an identity. An attacker who reaches that zone by any route — a compromised neighbor, SSRF through `process:web-api`, a stolen workload — originates gRPC calls that the ledger treats as authentic web-API traffic. Second-order: that identity commands transfers against `store:accounts-db` for every customer, so a foothold in a low-value zone converts directly into authority over `financial` assets in `boundary:core` and a crossing the model already flags.",
  "affected_element_ids": [
    "process:web-api",
    "process:ledger-service",
    "flow:web-api-to-ledger-service:post-transfer",
    "store:accounts-db"
  ],
  "evidence_refs": [
    "crossing:flow:web-api-to-ledger-service:post-transfer"
  ],
  "quotes": [
    {
      "text": "not authenticated and not encrypted",
      "source_label": "Payments platform notes"
    }
  ],
  "severity": {
    "likelihood": "medium",
    "impact": "high",
    "justification": "Likelihood is medium: no credential is needed, but the attacker must first hold a position in `boundary:dmz`. Impact is high: the impersonated identity drives transfers into `store:accounts-db` (`financial`, `pii`) across the whole customer base."
  },
  "mitigations": [
    {
      "summary": "Authenticate the ledger call regardless of zone",
      "detail": "Require mTLS or a workload-identity token on `flow:web-api-to-ledger-service:post-transfer`, so `process:ledger-service` verifies the caller rather than the source network."
    }
  ]
}
```

## Unknown-conditional: unverified settlement webhook

`flow:payments-provider-to-web-api:settlement-webhook` has `authentication: unknown`. The model does not say the webhook is unauthenticated — it says nobody recorded whether it is. Write the threat conditionally and name the attribute; the critic will mark it needs-info. Writing "the webhook is unauthenticated" would assert a fact the model does not contain.

```json
{
  "id": "S-03",
  "category": "spoofing",
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
