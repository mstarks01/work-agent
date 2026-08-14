# Session Management Exemplars

Two drafts against exemplar system A. A session cookie is stated, so the chapter applies; almost everything the chapter asks about it is unstated.

## V7.4.1 — No session timeout is stated for the customer session

Lifetime is two requirements, not one — idle and absolute — and this draft rules on one of them.

```json
{
  "requirement": "4.1",
  "title": "No session timeout is stated for the customer session",
  "description": "V7.4.1 asks that a session expires after a period of inactivity. It applies here because `flow:customer-to-web-api:submit-payment` carries a session cookie issued after a password login, so this system holds a session rather than authenticating each request independently. The notes say the cookie is issued and say nothing about when it stops being accepted. The requirement applies and the input does not settle it. The idle timeout `process:web-api` enforces would settle it.",
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
