# OAuth and OIDC Exemplars

Two drafts against exemplar system A. ASVS names this chapter as one an operator may ignore where there is no OAuth, and this system has none — so both drafts are exclusions, and each rests on a different stated fact.

## V10.4.1 — This system runs no OAuth or OIDC flow

The exclusion, resting on the authentication mechanisms the model states across all five flows.

```json
{
  "requirement": "4.1",
  "title": "This system runs no OAuth or OIDC flow",
  "description": "V10.4.1 governs the handling of an authorization code between a client and an authorization server. It does not apply here. No element in this model is an authorization server: the four elements are a web API, a ledger service, an accounts database and an audit bucket. The authentication mechanisms the flows state are a session cookie after a password login, an unstated webhook credential, network position, a shared static password and a service account. None of them is a delegated grant, so the requirement has no subject in this system.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "Customers sign in with an email and password and get a session cookie",
      "source_label": "Payments platform notes"
    }
  ]
}
```

## V10.4.3 — No authorization code lifetime applies without an authorization server

A second requirement in the chapter, excluded on the absence of the party it is about. Note that the draft names the element type it looked for.

```json
{
  "requirement": "4.3",
  "title": "No authorization code lifetime applies without an authorization server",
  "description": "V10.4.3 bounds the lifetime of an authorization code. It does not apply here, on the same stated fact that rules out V10.4.1: this model carries no authorization server and no client registered against one. `entity:payments-provider` is an external system that posts settlement confirmations to `process:web-api`, which is a webhook rather than a delegated authorization relationship. The requirement has no subject in this system.",
  "affected_element_ids": [
    "entity:payments-provider"
  ],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "Customers sign in with an email and password and get a session cookie",
      "source_label": "Payments platform notes"
    }
  ]
}
```
