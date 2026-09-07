# Cryptography Exemplars

Two drafts against exemplar system A. The first rests on an `unknown` attribute and is written conditionally; the second rests on a control the input names and asks the question the naming did not answer.

## V11.3.1 — No cipher mode is stated for data at rest in the accounts database

`encryption_at_rest` on the accounts database is never stated. That is a catalogued fact, so it is cited as a row rather than quoted.

```json
{
  "requirement": "3.1",
  "needs_evidence": "config",
  "title": "No cipher mode is stated for data at rest in the accounts database",
  "description": "V11.3.1 asks that no encryption uses an insecure block mode such as ECB or a weak padding scheme. It applies here because `store:accounts-db` is classified confidential and tagged `pii` and `financial`, so it holds data the standard expects to be encrypted. Its `encryption_at_rest` is never stated, so the input does not say whether encryption happens at all, let alone in which mode. The requirement applies and the input does not settle it — an `unknown` attribute is an unanswered question and not a missing control. Stating what protects `store:accounts-db` at rest would settle it.",
  "affected_element_ids": [
    "store:accounts-db"
  ],
  "evidence_refs": [
    "unknown:store:accounts-db:encryption_at_rest"
  ],
  "quotes": []
}
```

## V11.4.1 — No hash function is named for the password check or the session token

A different primitive from the cipher. The login and the cookie are stated; the functions behind them are not.

```json
{
  "requirement": "4.1",
  "needs_evidence": "code",
  "title": "No hash function is named for the password check or the session token",
  "description": "V11.4.1 asks that every cryptographic use of a hash — a signature, an HMAC, key derivation, random generation — uses an approved function and never a disallowed one such as MD5. It applies here because `process:web-api` verifies an email-and-password login and issues a session cookie on `flow:customer-to-web-api:submit-payment`, and a password verifier and a token generator each rest on a hash or a KDF. The notes name the mechanism and no function behind it. The requirement applies and the input does not settle it; the code in `process:web-api` that checks a password and mints a token would. This is a separate ruling from V11.3.1: an at-rest cipher mode and a hash function are different primitives.",
  "affected_element_ids": [
    "process:web-api",
    "flow:customer-to-web-api:submit-payment"
  ],
  "evidence_refs": [
    "crossing:flow:customer-to-web-api:submit-payment"
  ],
  "quotes": [
    {
      "text": "Customers sign in with an email and password and get a session cookie",
      "source_label": "Payments platform notes"
    }
  ]
}
```
