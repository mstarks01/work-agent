# Configuration (V13)

## Scope

Chapter V13 of ASVS 5.0: how the application and its environment are configured and built. Your lane covers secret management, dependency and supply-chain controls, the separation of environments, unnecessary features and services, and the configuration of the platform the application runs on.

Chapter boundaries: what a secret protects is chapter V11. What a running process is permitted to do is chapter V8. Your subject is what shipped and how it was set up.

## Applicability

This chapter applies to every application. Its evidence in the System Model is thin by design — a deployment's configuration is largely outside what a system description carries — so most rulings here are needs-info, and saying that plainly is the honest output rather than a weak one.

Read the model for what it does carry: elements tagged with secrets, a technology naming a package ecosystem, processes whose `exposure` says where they sit. Where the input names a component and nothing about its configuration, the requirement applies and stays open.

### The requirements of this chapter

21 requirements across 4 sections: 1 at level 1, 12 at level 2, 8 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V13.1 Configuration Documentation

- **V13.1.1** (L2) — Verify that all communication needs for the application are documented. This must include external services which the application relies upon and cases where an end user might be able to provide an external location to which the application will then connect.
- **V13.1.2** (L3) — Verify that for each service the application uses, the documentation defines the maximum number of concurrent connections (e.g., connection pool limits) and how the application behaves when that limit is reached, including any fallback or recovery mechanisms, to prevent denial of service conditions.
- **V13.1.3** (L3) — Verify that the application documentation defines resource‑management strategies for every external system or service it uses (e.g., databases, file handles, threads, HTTP connections). This should include resource‑release procedures, timeout settings, failure handling, and where retry logic is implemented, specifying retry limits, delays, and back‑off algorithms. For synchronous HTTP request–response operations it should mandate short timeouts and either disable retries or strictly limit retries to prevent cascading delays and resource exhaustion.
- **V13.1.4** (L3) — Verify that the application's documentation defines the secrets that are critical for the security of the application and a schedule for rotating them, based on the organization's threat model and business requirements.

#### V13.2 Backend Communication Configuration

- **V13.2.1** (L2) — Verify that communications between backend application components that don't support the application's standard user session mechanism, including APIs, middleware, and data layers, are authenticated. Authentication must use individual service accounts, short-term tokens, or certificate-based authentication and not unchanging credentials such as passwords, API keys, or shared accounts with privileged access.
- **V13.2.2** (L2) — Verify that communications between backend application components, including local or operating system services, APIs, middleware, and data layers, are performed with accounts assigned the least necessary privileges.
- **V13.2.3** (L2) — Verify that if a credential has to be used for service authentication, the credential being used by the consumer is not a default credential (e.g., root/root or admin/admin).
- **V13.2.4** (L2) — Verify that an allowlist is used to define the external resources or systems with which the application is permitted to communicate (e.g., for outbound requests, data loads, or file access). This allowlist can be implemented at the application layer, web server, firewall, or a combination of different layers.
- **V13.2.5** (L2) — Verify that the web or application server is configured with an allowlist of resources or systems to which the server can send requests or load data or files from.
- **V13.2.6** (L3) — Verify that where the application connects to separate services, it follows the documented configuration for each connection, such as maximum parallel connections, behavior when maximum allowed connections is reached, connection timeouts, and retry strategies.

#### V13.3 Secret Management

- **V13.3.1** (L2) — Verify that a secrets management solution, such as a key vault, is used to securely create, store, control access to, and destroy backend secrets. These could include passwords, key material, integrations with databases and third-party systems, keys and seeds for time-based tokens, other internal secrets, and API keys. Secrets must not be included in application source code or included in build artifacts. For an L3 application, this must involve a hardware-backed solution such as an HSM.
- **V13.3.2** (L2) — Verify that access to secret assets adheres to the principle of least privilege.
- **V13.3.3** (L3) — Verify that all cryptographic operations are performed using an isolated security module (such as a vault or hardware security module) to securely manage and protect key material from exposure outside of the security module.
- **V13.3.4** (L3) — Verify that secrets are configured to expire and be rotated based on the application's documentation.

#### V13.4 Unintended Information Leakage

- **V13.4.1** (L1) — Verify that the application is deployed either without any source control metadata, including the .git or .svn folders, or in a way that these folders are inaccessible both externally and to the application itself.
- **V13.4.2** (L2) — Verify that debug modes are disabled for all components in production environments to prevent exposure of debugging features and information leakage.
- **V13.4.3** (L2) — Verify that web servers do not expose directory listings to clients unless explicitly intended.
- **V13.4.4** (L2) — Verify that using the HTTP TRACE method is not supported in production environments, to avoid potential information leakage.
- **V13.4.5** (L2) — Verify that documentation (such as for internal APIs) and monitoring endpoints are not exposed unless explicitly intended.
- **V13.4.6** (L3) — Verify that the application does not expose detailed version information of backend components.
- **V13.4.7** (L3) — Verify that the web tier is configured to only serve files with specific file extensions to prevent unintentional information, configuration, and source code leakage.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**Secrets are named without their storage.** An element carrying a `secrets` asset tag with no stated key store is the clearest trigger this chapter has.
**Dependencies are assumed managed.** Requirements about a dependency inventory and its currency cannot be settled from a system description, and the answer is needs-info rather than absent.
**Environments are undifferentiated in the telling.** Where the input describes one deployment, the separation requirements are open rather than failed.
**Defaults are unmentioned.** Debug endpoints, sample content and default accounts are requirements here, and prose about a working system never mentions them.

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
