# Cryptography Exemplars

Two drafts against exemplar system A. The first rests on an `unknown` attribute and is written conditionally; the second rests on a control the input names and asks the question the naming did not answer.

## V11.3.1 — No cipher is stated for data at rest in the accounts database

`encryption_at_rest` on the accounts database is never stated. That is a catalogued fact, so it is cited as a row rather than quoted.

```json
{
  "requirement": "3.1",
  "needs_evidence": "config",
  "title": "No cipher is stated for data at rest in the accounts database",
  "description": "V11.3.1 governs the algorithm and mode used to encrypt stored data. It applies here because `store:accounts-db` is classified confidential and tagged `pii` and `financial`, so it holds data the standard expects to be protected. Its `encryption_at_rest` is never stated, so the input does not say whether encryption happens at all, let alone with which cipher. The requirement applies and the input does not settle it — an `unknown` attribute is an unanswered question and not a missing control. Stating what protects `store:accounts-db` at rest would settle it.",
  "affected_element_ids": [
    "store:accounts-db"
  ],
  "evidence_refs": [
    "unknown:store:accounts-db:encryption_at_rest"
  ],
  "quotes": []
}
```

## V11.4.1 — No key management is stated for the accounts database

Two requirements resting on one fact. Key management is a separate requirement from the cipher, and the same unstated attribute leaves both open — so each gets its own entry rather than one merged ruling.

```json
{
  "requirement": "4.1",
  "needs_evidence": "people",
  "title": "No key management is stated for the accounts database",
  "description": "V11.4.1 asks how cryptographic keys are generated, stored and rotated. It applies here because `store:accounts-db` is classified confidential and tagged `pii` and `financial`, so keys would exist wherever it is protected. Its `encryption_at_rest` is never stated, so the input names no key, no key store and no rotation period. This is a separate ruling from V11.3.1 and not a restatement of it: naming the cipher would leave key handling open, and naming the key store would leave the cipher open.",
  "affected_element_ids": [
    "store:accounts-db"
  ],
  "evidence_refs": [
    "unknown:store:accounts-db:encryption_at_rest"
  ],
  "quotes": []
}
```
