# Data Protection (V14)

## Scope

Chapter V14 of ASVS 5.0: the handling of data the application holds on a subject's behalf. Your lane covers data classification, the controls that follow from it, client-side caching and storage of sensitive values, and the requirements on retention and deletion.

Chapter boundaries: the cipher protecting data is chapter V11. The link carrying it is chapter V12. Who may read it is chapter V8. Your subject is the data's own classification and lifecycle.

## Applicability

This chapter applies wherever the application holds data that matters, which the model records directly: `DataStore.data_classification` and the `assets` tags on every element are the standard's own classification question already answered in part.

One group of requirements needs a browser: where sensitive data reaches a client, the caching and client-storage requirements apply, and where no browser exists they do not. Use the same evidence chapter V3 uses, and rule them out on the same fact.

### The requirements of this chapter

13 requirements across 3 sections: 2 at level 1, 7 at level 2, 4 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V14.1 Data Protection Documentation

- **V14.1.1** (L2) — Verify that all sensitive data created and processed by the application has been identified and classified into protection levels. This includes data that is only encoded and therefore easily decoded, such as Base64 strings or the plaintext payload inside a JWT. Protection levels need to take into account any data protection and privacy regulations and standards which the application is required to comply with.
- **V14.1.2** (L2) — Verify that all sensitive data protection levels have a documented set of protection requirements. This must include (but not be limited to) requirements related to general encryption, integrity verification, retention, how the data is to be logged, access controls around sensitive data in logs, database-level encryption, privacy and privacy-enhancing technologies to be used, and other confidentiality requirements.

#### V14.2 General Data Protection

- **V14.2.1** (L1) — Verify that sensitive data is only sent to the server in the HTTP message body or header fields, and that the URL and query string do not contain sensitive information, such as an API key or session token.
- **V14.2.2** (L2) — Verify that the application prevents sensitive data from being cached in server components, such as load balancers and application caches, or ensures that the data is securely purged after use.
- **V14.2.3** (L2) — Verify that defined sensitive data is not sent to untrusted parties (e.g., user trackers) to prevent unwanted collection of data outside of the application's control.
- **V14.2.4** (L2) — Verify that controls around sensitive data related to encryption, integrity verification, retention, how the data is to be logged, access controls around sensitive data in logs, privacy and privacy-enhancing technologies, are implemented as defined in the documentation for the specific data's protection level.
- **V14.2.5** (L3) — Verify that caching mechanisms are configured to only cache responses which have the expected content type for that resource and do not contain sensitive, dynamic content. The web server should return a 404 or 302 response when a non-existent file is accessed rather than returning a different, valid file. This should prevent Web Cache Deception attacks.
- **V14.2.6** (L3) — Verify that the application only returns the minimum required sensitive data for the application's functionality. For example, only returning some of the digits of a credit card number and not the full number. If the complete data is required, it should be masked in the user interface unless the user specifically views it.
- **V14.2.7** (L3) — Verify that sensitive information is subject to data retention classification, ensuring that outdated or unnecessary data is deleted automatically, on a defined schedule, or as the situation requires.
- **V14.2.8** (L3) — Verify that sensitive information is removed from the metadata of user-submitted files unless storage is consented to by the user.

#### V14.3 Client-side Data Protection

- **V14.3.1** (L1) — Verify that authenticated data is cleared from client storage, such as the browser DOM, after the client or session is terminated. The 'Clear-Site-Data' HTTP response header field may be able to help with this but the client-side should also be able to clear up if the server connection is not available when the session is terminated.
- **V14.3.2** (L2) — Verify that the application sets sufficient anti-caching HTTP response header fields (i.e., Cache-Control: no-store) so that sensitive data is not cached in browsers.
- **V14.3.3** (L2) — Verify that data stored in browser storage (such as localStorage, sessionStorage, IndexedDB, or cookies) does not contain sensitive data, with the exception of session tokens.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**Data is classified and the controls that follow are not stated.** A store marked confidential with no stated handling rule is the chapter's ordinary ruling.
**Retention is unmentioned.** How long data is kept and how it is deleted are requirements, and prose describing a system almost never carries either.
**Sensitive data reaches a browser.** Where a web frontend carries classified data, the cache-control and client-storage requirements apply and are open.
**No client surface.** Rule the browser-facing requirements out and name the flows and element types that decided it.

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
