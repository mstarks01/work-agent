# File Handling Exemplars

Two drafts against exemplar system A. Nothing in this system accepts, generates or serves a file, so both drafts are exclusions: the first rules out a requirement that presupposes an upload, and the second shows how a requirement about stored files rests on the same absence without restating it. Both name the missing thing in `absent_elements`. That is the whole of what grounds an exclusion: the other ground kinds can only name something present, so a quote borrowed from an unrelated part of the model would justify nothing.

## V5.2.1 — This system accepts no uploaded file

The chapter needs an upload path. The model has none — no flow carries a file and no store receives one from an untrusted caller — so the requirement is ruled out on the flows the model states.

```json
{
  "requirement": "2.1",
  "needs_evidence": "",
  "title": "This system accepts no uploaded file",
  "description": "V5.2.1 asks that the application accepts only files of a size it can process without losing performance or falling over. It does not apply here. The five flows in this model carry payment instructions, settlement confirmations, transfer instructions, balances and audit records; none carries a file from an untrusted source, and no element is described as receiving an attachment or a multipart body. `store:audit-log` is an append-only bucket written by `process:ledger-service`, which is an internal writer rather than an upload path. The requirement has no subject in this system.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "absent_elements": [
    "upload",
    "multipart",
    "attachment"
  ],
  "quotes": []
}
```

## V5.3.1 — No untrusted file sits where a request could execute it

The same absence decides a second requirement, and the draft says so in one clause rather than repeating the argument. A reader citing V5.3.1 gets its own entry.

```json
{
  "requirement": "3.1",
  "needs_evidence": "",
  "title": "No untrusted file sits where a request could execute it",
  "description": "V5.3.1 asks that a file which arrived from untrusted input, or was generated from it, and sits in a public folder is never run as server-side code when it is fetched over HTTP. It does not apply here, on the same stated fact that rules out V5.2.1: no flow in this model carries a file from an untrusted source, no element generates one from untrusted input, and no element serves stored file content to a caller. The requirement has no subject in this system.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "absent_elements": [
    "upload"
  ],
  "quotes": []
}
```
