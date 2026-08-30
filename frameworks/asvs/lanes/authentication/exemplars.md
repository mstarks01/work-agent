# Authentication Exemplars

Two drafts against exemplar system A. The first is written plainly because the submitter answered the question; the second is written conditionally because an attribute reads `unknown`. Keeping those two apart is most of the work in this lane.

## V6.3.1 — Customer authentication is single-factor by the submitter's account

`none` is an answer. The notes say MFA was never added, so this ruling is not conditional on anything and does not ask a question.

```json
{
  "requirement": "3.1",
  "needs_evidence": "",
  "title": "Customer authentication is single-factor by the submitter's account",
  "description": "V6.3.1 asks that multi-factor authentication is available and enforced according to the application's own security documentation. It applies here because `entity:customer` is a human external entity authenticated by `process:web-api` over `flow:customer-to-web-api:submit-payment`. The submitter states the answer rather than leaving it open: the mechanism is an email and password yielding a session cookie, and MFA was never added. So the input settles that no second factor exists on the customer path. What the input does not carry is the security documentation the requirement measures against, and that half remains open.",
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

## V6.2.1 — No password length policy is stated for customer accounts

A password exists, so the password requirements apply. Their subject is a parameter — a minimum length — and the input carries no parameter at all.

```json
{
  "requirement": "2.1",
  "needs_evidence": "config",
  "title": "No password length policy is stated for customer accounts",
  "description": "V6.2.1 sets a floor on password length. It applies here because `flow:customer-to-web-api:submit-payment` authenticates `entity:customer` with an email and a password, so this system runs password authentication. The notes describe the mechanism and carry no parameter of it: no minimum length, no maximum, and no statement about what the registration form accepts. The requirement applies and the input does not settle it. The password policy `process:web-api` enforces at registration would settle it.",
  "affected_element_ids": [
    "entity:customer",
    "process:web-api"
  ],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "Customers sign in with an email and password",
      "source_label": "Payments platform notes"
    }
  ]
}
```
