# Web Frontend Security Exemplars

Two drafts against exemplar system A. This chapter turns on one question the model can answer — is there a browser — so both drafts name the evidence for it.

## V3.3.1 — The customer session cookie's Secure attribute and prefix are never stated

A session cookie is named outright. Its attributes and its name are not, and each attribute is its own requirement rather than one ruling about cookies.

```json
{
  "requirement": "3.1",
  "needs_evidence": "config",
  "title": "The customer session cookie's Secure attribute and prefix are never stated",
  "description": "V3.3.1 asks that a cookie carries the `Secure` attribute and that its name carries the `__Host-` prefix, or failing that the `__Secure-` prefix. It applies here because `flow:customer-to-web-api:submit-payment` authenticates `entity:customer` with a session cookie issued after a password login, which is a browser-delivered credential and puts this system inside the chapter's scope. The notes name the cookie and say nothing about its attributes or its name; `HttpOnly` and `SameSite` are other requirements in this section and are not ruled here. The requirement applies and the input does not settle it. The `Set-Cookie` header the web API actually emits would settle it.",
  "affected_element_ids": [
    "entity:customer",
    "process:web-api",
    "flow:customer-to-web-api:submit-payment"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment"
  ],
  "quotes": [
    {
      "text": "Customers sign in with an email and password and get a session cookie; we never added MFA.",
      "source_label": "Payments platform notes"
    }
  ]
}
```

## V3.4.1 — No HSTS policy is described for the web API

The HSTS requirement, one header rather than the family. The chapter's precondition is met, so the requirement applies; the input describes a working system and no working-system description ever lists its headers.

```json
{
  "requirement": "4.1",
  "needs_evidence": "config",
  "title": "No HSTS policy is described for the web API",
  "description": "V3.4.1 asks that every response carries a Strict-Transport-Security header with a maximum age of at least a year, and from level 2 one that covers subdomains too. It applies here for the same reason V3.3.1 does: `entity:customer` is a human reaching `process:web-api` over HTTPS with a session cookie, so a browser is the client. The notes describe what the web API does with a payment and never describe what it sends in a response header. The requirement applies and the input does not settle it. A capture of one response from `process:web-api` would settle it, and the other response headers in this section are their own rulings.",
  "affected_element_ids": [
    "process:web-api"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment"
  ],
  "quotes": []
}
```
