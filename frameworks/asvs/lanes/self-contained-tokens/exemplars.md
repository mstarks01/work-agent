# Self-contained Tokens Exemplars

Two drafts against exemplar system A. This chapter's precondition fails here, and the first draft shows the exclusion resting on the credential the model *does* state rather than on a missing word.

## V9.1.2 — This system carries no self-contained token

A session cookie is an opaque reference, not a self-contained token. That is the fact that rules the chapter out.

```json
{
  "requirement": "1.2",
  "needs_evidence": "",
  "title": "This system carries no self-contained token",
  "description": "V9.1.2 governs the algorithms a verifier accepts when checking a self-contained token's signature. It does not apply here. The credentials this model states are a session cookie issued after a password login on `flow:customer-to-web-api:submit-payment`, a shared static password on `flow:ledger-service-to-accounts-db:read-write-balances`, and a service account on `flow:ledger-service-to-audit-log:append-transfer-record`. None of those carries its own claims, and no element's technology names a JWT, a JWS or an OIDC identity token. The requirement has no subject in this system.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "absent_elements": [
    "jwt"
  ],
  "quotes": [
    {
      "text": "get a session cookie",
      "source_label": "Payments platform notes"
    }
  ]
}
```

## V9.2.1 — No token validity window applies, and the one open credential does not change that

The webhook is the one place a token might have hidden, and its `authentication` reads `unknown`. That is not enough to bring the chapter in — but the draft says why the exclusion is safe rather than assuming it.

```json
{
  "requirement": "2.1",
  "needs_evidence": "",
  "title": "No token validity window applies, and the one open credential does not change that",
  "description": "V9.2.1 asks that a self-contained token carrying a validity window is accepted only while the verification time falls inside it — for a JWT, that the `nbf` and `exp` claims are checked. It does not apply here. The only credential this model leaves open is `authentication` on `flow:payments-provider-to-web-api:settlement-webhook`, which is never stated. An unstated credential is not evidence that a self-contained token exists, and no other flow or element names one. If the settlement webhook turns out to carry a signed token, this chapter applies and this ruling should be revisited.",
  "affected_element_ids": [
    "flow:payments-provider-to-web-api:settlement-webhook"
  ],
  "evidence_refs": [
    "unknown:flow:payments-provider-to-web-api:settlement-webhook:authentication"
  ],
  "absent_elements": [
    "jwt"
  ],
  "quotes": []
}
```
