# Authentication Exemplars

Two drafts against exemplar system A. A password login is stated, so both requirements apply, and both are open: the notes name no defence around the login and no parameter of the password. Neither draft turns the one thing the submitter did settle — no MFA — into a ruling about a requirement that asks something else.

## V6.3.1 — No control against credential stuffing or brute force is stated for the customer login

The notes settle that MFA was never added. That answers a different requirement; this one asks what stands between an attacker and the password form, and the notes say nothing about it.

```json
{
  "requirement": "3.1",
  "needs_evidence": "config",
  "title": "No control against credential stuffing or brute force is stated for the customer login",
  "description": "V6.3.1 asks that the defences the application's security documentation prescribes against credential stuffing and password brute force are actually in place. It applies here because `entity:customer` authenticates to `process:web-api` with an email and password over `flow:customer-to-web-api:submit-payment`, so a password login exists to attack. The notes describe the mechanism and say nothing about rate limiting, lockout or any other defence, and they carry no security documentation to measure against. The absence of MFA is a different fact and settles nothing here. The requirement applies and the input does not settle it; the login configuration `process:web-api` enforces would.",
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
