# Injection Sinks and the Context That Decides Encoding

## When this applies

The model names a query-driven store, or an element that accepts authored text from a person. Chapter V1 asks how data is encoded on the way *out* to an interpreter, and how untrusted markup is sanitized on the way *in*.

## What to look for

- **Name the interpreter, not the language.** V1 splits by sink: a SQL store, an OS command, an LDAP query, an XPath expression and an HTML document are separate requirements because the escaping differs. A model naming Postgres puts the SQL requirements in scope; it says nothing about the command-injection ones unless something shells out.
- **Output encoding is contextual.** The same string needs different treatment inside an HTML element, an attribute, a URL, a CSS block and a script block. A description saying "we escape HTML" leaves the context question open rather than closing it.
- **Authored text is the sanitization case.** A comment box, a profile bio, a rich-text field or anything rendered back to another user brings the sanitization requirements into scope. A field that only ever round-trips to its own author still reaches a parser.
- **Canonicalization runs once, and before validation.** The order is itself a requirement. A description that mentions decoding, unescaping or normalizing is worth a ruling on whether it happens before the value is checked.
- **Silence about a store's driver is normal.** Almost no submitted description names its query mechanism. That makes the requirement applicable and unsettled, which is a ruling, not a gap.

## Guardrails

- Analysis knowledge, not evidence. Ground every ruling in what the model or the submitter's prose actually says, never in this note.
- Rule applicability, never a pass. "Uses an ORM" does not confirm the requirement is satisfied — an ORM has raw-query escapes, and the input never showed the code.
- Stay in V1. What the application *does* with a validated value is chapter V2; who may send it is V8.
