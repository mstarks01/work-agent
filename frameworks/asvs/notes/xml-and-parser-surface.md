# XML, Schemas and What a Parser Will Fetch

## When this applies

The model names XML, SOAP, SVG, DOCX, XLSX, RSS, a sitemap, or any format that reaches an XML parser. Chapter V1 carries the external-entity and schema requirements, and they apply wherever such a parser runs, not only where a submitter used the word XML.

## What to look for

- **The parser is the subject, not the payload.** An SVG avatar, an office document and a SAML assertion all reach an XML parser. A model naming any of them puts these requirements in scope even if it never says XML.
- **Two separate questions.** External *entity* resolution is one requirement; fetching an external *schema* or DTD is another. A description saying entities are disabled leaves the schema question open.
- **Where the fetch goes.** An entity resolves to a URL the parser will retrieve, so the reachable network decides the impact — a cloud metadata endpoint, an internal service, a file path. That is what makes this an outbound-request question as much as a parsing one.
- **Defaults changed, and defaults are per library.** Many parsers now refuse external entities out of the box and many do not, and the model never names the library. Applicable and unsettled is the honest ruling.
- **SOAP brings its own chapter neighbours.** A SOAP service is also an API surface, so V4 applies beside this — file two rulings under two chapters rather than one that spans both.

## Guardrails

- Analysis knowledge, not evidence. The finding rests on what the input said about parsing, never on this note.
- Rule applicability, never a pass. A named hardened library does not confirm its configuration; the configuration is not in the material.
- If nothing in the model reaches an XML parser, the honest answer is that the chapter's XML requirements do not apply, recorded as such rather than left silent.
