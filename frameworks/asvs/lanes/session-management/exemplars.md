# Session Management Exemplars

Two drafts against exemplar system A. A session cookie is stated, so the chapter applies; almost everything the chapter asks about it is unstated.

## V7.4.1 — Nothing says a terminated customer session stops being accepted

Termination is the subject, not lifetime: what the backend does once logout or expiry has happened. A session that lives on after either is the failure this requirement names.

```json
{
  "requirement": "4.1",
  "needs_evidence": "code",
  "title": "Nothing says a terminated customer session stops being accepted",
  "description": "V7.4.1 asks that once a session is terminated — by logout or by expiry — the application refuses any further use of it, which for a stateful session means invalidating it at the backend. It applies here because `flow:customer-to-web-api:submit-payment` carries a session cookie issued after a password login, so this system holds a session rather than authenticating each request independently. The notes say the cookie is issued and say nothing about logout, expiry, or whether `process:web-api` invalidates the session record when either happens. The requirement applies and the input does not settle it; the session handling in `process:web-api` would.",
  "affected_element_ids": [
    "entity:customer",
    "process:web-api",
    "flow:customer-to-web-api:submit-payment"
  ],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "get a session cookie",
      "source_label": "Payments platform notes"
    }
  ]
}
```
## V7.2.3 — The session token's generation is never described

Generation is the requirement the input can least often settle, and assuming a framework default would be inventing a fact the model does not state.

```json
{
  "requirement": "2.3",
  "needs_evidence": "code",
  "title": "The session token's generation is never described",
  "description": "V7.2.3 sets a floor on the entropy of a session token and asks how it is generated. It applies here for the same reason V7.4.1 does: a session cookie is issued to `entity:customer` after a password login. The notes name the cookie and describe neither its length, its alphabet, nor the generator behind it. The requirement applies and the input does not settle it, and the framework `process:web-api` runs on is not evidence about the token — a stated generator would be.",
  "affected_element_ids": [
    "process:web-api",
    "flow:customer-to-web-api:submit-payment"
  ],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "get a session cookie",
      "source_label": "Payments platform notes"
    }
  ]
}
```
