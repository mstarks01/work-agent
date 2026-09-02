# File Handling Exemplars

Two drafts against exemplar system A. This chapter's precondition fails for this system, so the first draft is the chapter-wide exclusion and the second shows how a second requirement rests on the same absence without restating it. Both name the missing thing in `absent_elements`. That is the whole of what grounds an exclusion: the other ground kinds can only name something present, so a quote borrowed from an unrelated part of the model would justify nothing.

## V5.2.1 — This system accepts no uploaded file

The chapter needs an upload path. The model has none — no flow carries a file and no store receives one from an untrusted caller — so the requirement is ruled out on the flows the model states.

```json
{
  "requirement": "2.1",
  "needs_evidence": "",
  "title": "This system accepts no uploaded file",
  "description": "V5.2.1 governs the validation of an uploaded file's content and type. It does not apply here. The five flows in this model carry payment instructions, settlement confirmations, transfer instructions, balances and audit records; none carries a file from an untrusted source, and no element is described as receiving an attachment or a multipart body. `store:audit-log` is an append-only bucket written by `process:ledger-service`, which is an internal writer rather than an upload path. The requirement has no subject in this system.",
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

## V5.3.1 — No uploaded file is served back to a client

The same absence decides a second requirement, and the draft says so in one clause rather than repeating the argument. A reader citing V5.3.1 gets its own entry.

```json
{
  "requirement": "3.1",
  "needs_evidence": "",
  "title": "No uploaded file is served back to a client",
  "description": "V5.3.1 governs how stored files are served back, so that an uploaded document cannot be executed or interpreted by the browser that fetches it. It does not apply here, on the same stated fact that rules out V5.2.1: no flow in this model carries a file from an untrusted source, and no element serves stored file content to a caller. The requirement has no subject in this system.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "absent_elements": [
    "upload"
  ],
  "quotes": []
}
```
