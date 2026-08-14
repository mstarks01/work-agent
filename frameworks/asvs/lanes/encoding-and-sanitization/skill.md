# Encoding and Sanitization (V1)

## Scope

Chapter V1 of ASVS 5.0: output encoding, injection prevention and the sanitization of untrusted content. Your lane covers every place the application builds an interpreted string from data it did not author — SQL and other query languages, HTML and the DOM, URLs, JavaScript and JSON, operating-system commands, LDAP, template engines, XML and file paths — plus the canonicalization that has to happen before any of it.

Chapter boundaries: whether an input is *accepted at all* is chapter V2. Whether a file's contents are safe to store is chapter V5. What a browser does with a response header is chapter V3. Your subject is the moment data crosses into an interpreter.

## Applicability

This chapter applies to every application, because every application builds at least one interpreted string. The presence tests inside it are finer: a requirement about parameterized queries needs a query-driven store, one about rich text needs an application that accepts authored content, and one about XML needs a parser.

The System Model tells you which interpreters exist through free text — a `technology` naming a database engine, a `protocol` naming SOAP, a `data_description` naming user-authored content. Where the input names no interpreter of a given kind, the requirement about it is one you rule does not apply, and you say which absence decided it.

### The requirements of this chapter

30 requirements across 5 sections: 8 at level 1, 19 at level 2, 3 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V1.1 Encoding and Sanitization Architecture

- **V1.1.1** (L2) — Verify that input is decoded or unescaped into a canonical form only once, it is only decoded when encoded data in that form is expected, and that this is done before processing the input further, for example it is not performed after input validation or sanitization.
- **V1.1.2** (L2) — Verify that the application performs output encoding and escaping either as a final step before being used by the interpreter for which it is intended or by the interpreter itself.

#### V1.2 Injection Prevention

- **V1.2.1** (L1) — Verify that output encoding for an HTTP response, HTML document, or XML document is relevant for the context required, such as encoding the relevant characters for HTML elements, HTML attributes, HTML comments, CSS, or HTTP header fields, to avoid changing the message or document structure.
- **V1.2.2** (L1) — Verify that when dynamically building URLs, untrusted data is encoded according to its context (e.g., URL encoding or base64url encoding for query or path parameters). Ensure that only safe URL protocols are permitted (e.g., disallow javascript: or data:).
- **V1.2.3** (L1) — Verify that output encoding or escaping is used when dynamically building JavaScript content (including JSON), to avoid changing the message or document structure (to avoid JavaScript and JSON injection).
- **V1.2.4** (L1) — Verify that data selection or database queries (e.g., SQL, HQL, NoSQL, Cypher) use parameterized queries, ORMs, entity frameworks, or are otherwise protected from SQL Injection and other database injection attacks. This is also relevant when writing stored procedures.
- **V1.2.5** (L1) — Verify that the application protects against OS command injection and that operating system calls use parameterized OS queries or use contextual command line output encoding.
- **V1.2.6** (L2) — Verify that the application protects against LDAP injection vulnerabilities, or that specific security controls to prevent LDAP injection have been implemented.
- **V1.2.7** (L2) — Verify that the application is protected against XPath injection attacks by using query parameterization or precompiled queries.
- **V1.2.8** (L2) — Verify that LaTeX processors are configured securely (such as not using the "--shell-escape" flag) and an allowlist of commands is used to prevent LaTeX injection attacks.
- **V1.2.9** (L2) — Verify that the application escapes special characters in regular expressions (typically using a backslash) to prevent them from being misinterpreted as metacharacters.
- **V1.2.10** (L3) — Verify that the application is protected against CSV and Formula Injection. The application must follow the escaping rules defined in RFC 4180 sections 2.6 and 2.7 when exporting CSV content. Additionally, when exporting to CSV or other spreadsheet formats (such as XLS, XLSX, or ODF), special characters (including '=', '+', '-', '@', '\t' (tab), and '\0' (null character)) must be escaped with a single quote if they appear as the first character in a field value.

#### V1.3 Sanitization

- **V1.3.1** (L1) — Verify that all untrusted HTML input from WYSIWYG editors or similar is sanitized using a well-known and secure HTML sanitization library or framework feature.
- **V1.3.2** (L1) — Verify that the application avoids the use of eval() or other dynamic code execution features such as Spring Expression Language (SpEL). Where there is no alternative, any user input being included must be sanitized before being executed.
- **V1.3.3** (L2) — Verify that data being passed to a potentially dangerous context is sanitized beforehand to enforce safety measures, such as only allowing characters which are safe for this context and trimming input which is too long.
- **V1.3.4** (L2) — Verify that user-supplied Scalable Vector Graphics (SVG) scriptable content is validated or sanitized to contain only tags and attributes (such as draw graphics) that are safe for the application, e.g., do not contain scripts and foreignObject.
- **V1.3.5** (L2) — Verify that the application sanitizes or disables user-supplied scriptable or expression template language content, such as Markdown, CSS or XSL stylesheets, BBCode, or similar.
- **V1.3.6** (L2) — Verify that the application protects against Server-side Request Forgery (SSRF) attacks, by validating untrusted data against an allowlist of protocols, domains, paths and ports and sanitizing potentially dangerous characters before using the data to call another service.
- **V1.3.7** (L2) — Verify that the application protects against template injection attacks by not allowing templates to be built based on untrusted input. Where there is no alternative, any untrusted input being included dynamically during template creation must be sanitized or strictly validated.
- **V1.3.8** (L2) — Verify that the application appropriately sanitizes untrusted input before use in Java Naming and Directory Interface (JNDI) queries and that JNDI is configured securely to prevent JNDI injection attacks.
- **V1.3.9** (L2) — Verify that the application sanitizes content before it is sent to memcache to prevent injection attacks.
- **V1.3.10** (L2) — Verify that format strings which might resolve in an unexpected or malicious way when used are sanitized before being processed.
- **V1.3.11** (L2) — Verify that the application sanitizes user input before passing to mail systems to protect against SMTP or IMAP injection.
- **V1.3.12** (L3) — Verify that regular expressions are free from elements causing exponential backtracking, and ensure untrusted input is sanitized to mitigate ReDoS or Runaway Regex attacks.

#### V1.4 Memory, String, and Unmanaged Code

- **V1.4.1** (L2) — Verify that the application uses memory-safe string, safer memory copy and pointer arithmetic to detect or prevent stack, buffer, or heap overflows.
- **V1.4.2** (L2) — Verify that sign, range, and input validation techniques are used to prevent integer overflows.
- **V1.4.3** (L2) — Verify that dynamically allocated memory and resources are released, and that references or pointers to freed memory are removed or set to null to prevent dangling pointers and use-after-free vulnerabilities.

#### V1.5 Safe Deserialization

- **V1.5.1** (L1) — Verify that the application configures XML parsers to use a restrictive configuration and that unsafe features such as resolving external entities are disabled to prevent XML eXternal Entity (XXE) attacks.
- **V1.5.2** (L2) — Verify that deserialization of untrusted data enforces safe input handling, such as using an allowlist of object types or restricting client-defined object types, to prevent deserialization attacks. Deserialization mechanisms that are explicitly defined as insecure must not be used with untrusted input.
- **V1.5.3** (L3) — Verify that different parsers used in the application for the same data type (e.g., JSON parsers, XML parsers, URL parsers), perform parsing in a consistent way and use the same character encoding mechanism to avoid issues such as JSON Interoperability vulnerabilities or different URI or file parsing behavior being exploited in Remote File Inclusion (RFI) or Server-side Request Forgery (SSRF) attacks.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**The interpreter is named and the encoding is not.** The input describes a store, a template engine or a query path and says nothing about how strings reach it. This is the ordinary case here: the requirement applies and the input does not settle it.
**Validation is offered as encoding.** The input says data is validated on the way in and nothing about escaping on the way out. The standard treats those as separate activities, and a requirement about output encoding is not answered by an input rule.
**A canonicalization step is implied but never described.** Decoding once, before validation, is what several requirements here turn on. Prose almost never mentions it, which makes those requirements needs-info rather than absent.
**No interpreter of the kind exists.** A batch pipeline with no HTML surface has nothing to answer the DOM requirements. Rule those out and name the fact — the absence of a browser-facing element — that rules them out.

## Guardrails

- **Rule the requirement, do not restate it.** A claim whose description repeats the published text has said nothing about this system. Name the fact of *this* model that makes the requirement apply, and what the input does or does not show about it.
- **Unknown is not absent.** When an attribute reads `unknown`, the control is unverified. Write the ruling conditionally, cite the element and the attribute, and let the critic mark it needs-info. An attribute reading `none` is the opposite: the submitter answered, so write that ruling plainly.
- **Never report a pass.** The input carries prose, not source code or configuration, so "this requirement is satisfied" is not a conclusion available to you. Where the input describes a control that looks sufficient, say what it describes and what remains unverified.
- **Never use the word compliance.** This run rules on applicability, and a level-filtered run covers a subset of the standard. Neither is a compliance result.
- **Stay in the model.** Reference only element IDs the System Model carries. A requirement about a coding practice has no position in the graph — leave `affected_element_ids` empty rather than reaching for the nearest element.
- **One ruling per requirement.** Do not merge two requirements whose subjects are close: the standard separated them, and a reader cites them separately.

## Mitigations

This record carries no mitigations, and that is a decision rather than an omission: **the requirement text is the remedy**. A reader who wants to know what to do reads the requirement your claim cites, in the published standard, at the version your claim's ID names.

So do not write a countermeasure into the description. What belongs there is what the requirement's subject looks like *in this system* — which element, which attribute, which stated fact — because that is what the standard's text cannot supply and what makes the citation actionable.

Where a ruling is needs-info, write **the question**: the one fact the submitter could supply that would settle it.
