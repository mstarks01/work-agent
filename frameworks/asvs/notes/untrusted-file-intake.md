# Files Arriving From Outside

## When this applies

The model shows an element that accepts an uploaded file, an import, an attachment or a document drop. Chapter V5 covers what bounds the intake and what happens to the file afterwards.

## What to look for

- **Size and count come first.** A limit on file size, on total request size and on the number of files is the requirement, and it is the one most often unstated. Without it the upload path is also a resource question.
- **Type checked by content, not by name.** The extension and the client-supplied content type are attacker-controlled. The requirement is that the decision rests on what the file actually is.
- **Where it lands decides the rest.** A file written inside the web root, or served back from the same origin, brings a different set of requirements from one written to object storage and served through a separate domain.
- **The filename is a path.** Traversal sequences, absolute paths, reserved device names and unicode confusables in a stored name are their own requirement.
- **Downloading is an intake too.** A system that fetches a file from a URL a user supplied has an upload path in every respect that matters here, and it also raises the outbound-request question.
- **Decompression is a separate ruling.** An archive, an office document or an image that gets resized is processed by something, and expansion limits are a requirement of their own.

## Guardrails

- Analysis knowledge, not evidence. Name the element or flow the file arrives on.
- Rule applicability, never a pass. A stated antivirus scan does not satisfy the type, size or location requirements; they are separate.
- Storage protection is V14, and the transport that carried the file is V12. Keep the intake here.
