# Secure Communication (V12)

## Scope

Chapter V12 of ASVS 5.0: protecting data in transit. Your lane covers TLS versions and cipher suites, certificate validation on outbound connections, encryption of internal and backend links, and the requirement that no sensitive traffic runs unprotected.

Chapter boundaries: what the application encrypts itself is chapter V11. What a browser is told about transport is chapter V3. Your subject is the link between two elements.

## Applicability

This chapter applies wherever two elements exchange data over a network, which is every system with more than one element. Its structural evidence is the strongest in the standard for this service: `DataFlow.encryption_in_transit` and `DataFlow.protocol` are recorded per link, and the derived boundary crossings say which links leave a trust zone.

Cite those directly. A crossing whose `encryption_in_transit` reads `unknown` is a needs-info ruling with an element and an attribute already named; one reading `none` is a ruling you write plainly.

### The requirements of this chapter

12 requirements across 3 sections: 3 at level 1, 6 at level 2, 3 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V12.1 General TLS Security Guidance

- **V12.1.1** (L1) — Verify that only the latest recommended versions of the TLS protocol are enabled, such as TLS 1.2 and TLS 1.3. The latest version of the TLS protocol must be the preferred option.
- **V12.1.2** (L2) — Verify that only recommended cipher suites are enabled, with the strongest cipher suites set as preferred. L3 applications must only support cipher suites which provide forward secrecy.
- **V12.1.3** (L2) — Verify that the application validates that mTLS client certificates are trusted before using the certificate identity for authentication or authorization.
- **V12.1.4** (L3) — Verify that proper certification revocation, such as Online Certificate Status Protocol (OCSP) Stapling, is enabled and configured.
- **V12.1.5** (L3) — Verify that Encrypted Client Hello (ECH) is enabled in the application's TLS settings to prevent exposure of sensitive metadata, such as the Server Name Indication (SNI), during TLS handshake processes.

#### V12.2 HTTPS Communication with External Facing Services

- **V12.2.1** (L1) — Verify that TLS is used for all connectivity between a client and external facing, HTTP-based services, and does not fall back to insecure or unencrypted communications.
- **V12.2.2** (L1) — Verify that external facing services use publicly trusted TLS certificates.

#### V12.3 General Service to Service Communication Security

- **V12.3.1** (L2) — Verify that an encrypted protocol such as TLS is used for all inbound and outbound connections to and from the application, including monitoring systems, management tools, remote access and SSH, middleware, databases, mainframes, partner systems, or external APIs. The server must not fall back to insecure or unencrypted protocols.
- **V12.3.2** (L2) — Verify that TLS clients validate certificates received before communicating with a TLS server.
- **V12.3.3** (L2) — Verify that TLS or another appropriate transport encryption mechanism used for all connectivity between internal, HTTP-based services within the application, and does not fall back to insecure or unencrypted communications.
- **V12.3.4** (L2) — Verify that TLS connections between internal services use trusted certificates. Where internally generated or self-signed certificates are used, the consuming service must be configured to only trust specific internal CAs and specific self-signed certificates.
- **V12.3.5** (L3) — Verify that services communicating internally within a system (intra-service communications) use strong authentication to ensure that each endpoint is verified. Strong authentication methods, such as TLS client authentication, must be employed to ensure identity, using public-key infrastructure and mechanisms that are resistant to replay attacks. For microservice architectures, consider using a service mesh to simplify certificate management and enhance security.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**A crossing is unprotected or unstated.** This is the chapter's core ruling and the one the model answers best. One ruling per requirement, citing the flows that carry it.
**Internal links are treated as exempt.** Requirements here do not stop at the perimeter, and an internal flow with no stated protection is in scope.
**A TLS version is claimed without a floor.** "TLS" in the input does not answer a requirement about which versions are permitted.
**Outbound certificate validation is unmentioned.** Where the application calls an external system, whether it validates that party's certificate is its own requirement.

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
