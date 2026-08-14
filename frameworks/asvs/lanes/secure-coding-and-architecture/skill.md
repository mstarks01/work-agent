# Secure Coding and Architecture (V15)

## Scope

Chapter V15 of ASVS 5.0: the properties of the code and the shape of the system that hold across every other chapter. Your lane covers defensive coding practice, the safe handling of untrusted data inside the application, concurrency, third-party and generated code, and the architectural separation the standard still asks for after removing its architecture chapter.

Chapter boundaries: encoding at an interpreter is chapter V1. Configuration and dependency management is chapter V13. Your subject is how the code is written and how the system is divided.

## Applicability

This chapter applies to every application. It is the chapter with the least to read in a System Model, because its subject is code rather than structure — and that is a fact worth stating in a ruling rather than working around.

What the model does answer is separation: `trust_zone` on each element and the derived boundary crossings say how the system is divided, and the requirements about component separation rest on exactly that. For the rest, name the requirement, say the input carries prose rather than code, and let the ruling be needs-info.

### The requirements of this chapter

21 requirements across 4 sections: 3 at level 1, 10 at level 2, 8 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V15.1 Secure Coding and Architecture Documentation

- **V15.1.1** (L1) — Verify that application documentation defines risk based remediation time frames for 3rd party component versions with vulnerabilities and for updating libraries in general, to minimize the risk from these components.
- **V15.1.2** (L2) — Verify that an inventory catalog, such as software bill of materials (SBOM), is maintained of all third-party libraries in use, including verifying that components come from pre-defined, trusted, and continually maintained repositories.
- **V15.1.3** (L2) — Verify that the application documentation identifies functionality which is time-consuming or resource-demanding. This must include how to prevent a loss of availability due to overusing this functionality and how to avoid a situation where building a response takes longer than the consumer's timeout. Potential defenses may include asynchronous processing, using queues, and limiting parallel processes per user and per application.
- **V15.1.4** (L3) — Verify that application documentation highlights third-party libraries which are considered to be "risky components".
- **V15.1.5** (L3) — Verify that application documentation highlights parts of the application where "dangerous functionality" is being used.

#### V15.2 Security Architecture and Dependencies

- **V15.2.1** (L1) — Verify that the application only contains components which have not breached the documented update and remediation time frames.
- **V15.2.2** (L2) — Verify that the application has implemented defenses against loss of availability due to functionality which is time-consuming or resource-demanding, based on the documented security decisions and strategies for this.
- **V15.2.3** (L2) — Verify that the production environment only includes functionality that is required for the application to function, and does not expose extraneous functionality such as test code, sample snippets, and development functionality.
- **V15.2.4** (L3) — Verify that third-party components and all of their transitive dependencies are included from the expected repository, whether internally owned or an external source, and that there is no risk of a dependency confusion attack.
- **V15.2.5** (L3) — Verify that the application implements additional protections around parts of the application which are documented as containing "dangerous functionality" or using third-party libraries considered to be "risky components". This could include techniques such as sandboxing, encapsulation, containerization or network level isolation to delay and deter attackers who compromise one part of an application from pivoting elsewhere in the application.

#### V15.3 Defensive Coding

- **V15.3.1** (L1) — Verify that the application only returns the required subset of fields from a data object. For example, it should not return an entire data object, as some individual fields should not be accessible to users.
- **V15.3.2** (L2) — Verify that where the application backend makes calls to external URLs, it is configured to not follow redirects unless it is intended functionality.
- **V15.3.3** (L2) — Verify that the application has countermeasures to protect against mass assignment attacks by limiting allowed fields per controller and action, e.g., it is not possible to insert or update a field value when it was not intended to be part of that action.
- **V15.3.4** (L2) — Verify that all proxying and middleware components transfer the user's original IP address correctly using trusted data fields that cannot be manipulated by the end user, and the application and web server use this correct value for logging and security decisions such as rate limiting, taking into account that even the original IP address may not be reliable due to dynamic IPs, VPNs, or corporate firewalls.
- **V15.3.5** (L2) — Verify that the application explicitly ensures that variables are of the correct type and performs strict equality and comparator operations. This is to avoid type juggling or type confusion vulnerabilities caused by the application code making an assumption about a variable type.
- **V15.3.6** (L2) — Verify that JavaScript code is written in a way that prevents prototype pollution, for example, by using Set() or Map() instead of object literals.
- **V15.3.7** (L2) — Verify that the application has defenses against HTTP parameter pollution attacks, particularly if the application framework makes no distinction about the source of request parameters (query string, body parameters, cookies, or header fields).

#### V15.4 Safe Concurrency

- **V15.4.1** (L3) — Verify that shared objects in multi-threaded code (such as caches, files, or in-memory objects accessed by multiple threads) are accessed safely by using thread-safe types and synchronization mechanisms like locks or semaphores to avoid race conditions and data corruption.
- **V15.4.2** (L3) — Verify that checks on a resource's state, such as its existence or permissions, and the actions that depend on them are performed as a single atomic operation to prevent time-of-check to time-of-use (TOCTOU) race conditions. For example, checking if a file exists before opening it, or verifying a user’s access before granting it.
- **V15.4.3** (L3) — Verify that locks are used consistently to avoid threads getting stuck, whether by waiting on each other or retrying endlessly, and that locking logic stays within the code responsible for managing the resource to ensure locks cannot be inadvertently or maliciously modified by external classes or code.
- **V15.4.4** (L3) — Verify that resource allocation policies prevent thread starvation by ensuring fair access to resources, such as by leveraging thread pools, allowing lower-priority threads to proceed within a reasonable timeframe.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**The subject is code and the input is prose.** Most rulings here are needs-info for one reason, and stating it once per ruling is honest rather than repetitive.
**Third-party components are named without their handling.** Where the input names a library or a service, the requirements about untrusted code apply.
**Concurrency is implied by the architecture.** A model with a queue, a worker pool or a shared store raises the race-condition requirements even where the input never mentions them.
**Separation is stated in the model.** Where `trust_zone` divides the system, cite it. Where every element shares one zone, cite that instead — it is a stated fact, not a gap.

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
