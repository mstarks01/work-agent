# Authorization (V8)

## Scope

Chapter V8 of ASVS 5.0: what an established identity is permitted to do. Your lane covers the authorization model itself, enforcement at a trusted layer, function- and object-level access control, and the documentation of the rules being enforced.

Chapter boundaries: who the caller is is chapter V6. How a token carries a permission claim is chapter V9. Whether a request is well formed is chapter V2. Your subject is the decision to allow or deny.

## Applicability

This chapter applies to every application that distinguishes between callers at all, which is nearly all of them. Its one structural read is where enforcement happens: a rule applied on an untrusted side is not a control.

The System Model carries this through `trust_zone` and through `ExternalEntity.kind`. A flow crossing from a public zone into a service zone is where the decision has to be made, and a boundary crossing in the derived list is the fact to cite.

### The requirements of this chapter

13 requirements across 4 sections: 4 at level 1, 3 at level 2, 6 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V8.1 Authorization Documentation

- **V8.1.1** (L1) — Verify that authorization documentation defines rules for restricting function-level and data-specific access based on consumer permissions and resource attributes.
- **V8.1.2** (L2) — Verify that authorization documentation defines rules for field-level access restrictions (both read and write) based on consumer permissions and resource attributes. Note that these rules might depend on other attribute values of the relevant data object, such as state or status.
- **V8.1.3** (L3) — Verify that the application's documentation defines the environmental and contextual attributes (including but not limited to, time of day, user location, IP address, or device) that are used in the application to make security decisions, including those pertaining to authentication and authorization.
- **V8.1.4** (L3) — Verify that authentication and authorization documentation defines how environmental and contextual factors are used in decision-making, in addition to function-level, data-specific, and field-level authorization. This should include the attributes evaluated, thresholds for risk, and actions taken (e.g., allow, challenge, deny, step-up authentication).

#### V8.2 General Authorization Design

- **V8.2.1** (L1) — Verify that the application ensures that function-level access is restricted to consumers with explicit permissions.
- **V8.2.2** (L1) — Verify that the application ensures that data-specific access is restricted to consumers with explicit permissions to specific data items to mitigate insecure direct object reference (IDOR) and broken object level authorization (BOLA).
- **V8.2.3** (L2) — Verify that the application ensures that field-level access is restricted to consumers with explicit permissions to specific fields to mitigate broken object property level authorization (BOPLA).
- **V8.2.4** (L3) — Verify that adaptive security controls based on a consumer's environmental and contextual attributes (such as time of day, location, IP address, or device) are implemented for authentication and authorization decisions, as defined in the application's documentation. These controls must be applied when the consumer tries to start a new session and also during an existing session.

#### V8.3 Operation Level Authorization

- **V8.3.1** (L1) — Verify that the application enforces authorization rules at a trusted service layer and doesn't rely on controls that an untrusted consumer could manipulate, such as client-side JavaScript.
- **V8.3.2** (L3) — Verify that changes to values on which authorization decisions are made are applied immediately. Where changes cannot be applied immediately, (such as when relying on data in self-contained tokens), there must be mitigating controls to alert when a consumer performs an action when they are no longer authorized to do so and revert the change. Note that this alternative would not mitigate information leakage.
- **V8.3.3** (L3) — Verify that access to an object is based on the originating subject's (e.g. consumer's) permissions, not on the permissions of any intermediary or service acting on their behalf. For example, if a consumer calls a web service using a self-contained token for authentication, and the service then requests data from a different service, the second service will use the consumer's token, rather than a machine-to-machine token from the first service, to make permission decisions.

#### V8.4 Other Authorization Considerations

- **V8.4.1** (L2) — Verify that multi-tenant applications use cross-tenant controls to ensure consumer operations will never affect tenants with which they do not have permissions to interact.
- **V8.4.2** (L3) — Verify that access to administrative interfaces incorporates multiple layers of security, including continuous consumer identity verification, device security posture assessment, and contextual risk analysis, ensuring that network location or trusted endpoints are not the sole factors for authorization even though they may reduce the likelihood of unauthorized access.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**Roles are named without a decision point.** The input names user classes and never says which element enforces the distinction. That is the chapter's most common ruling.
**Object-level access is not distinguished from function-level.** A system that says who may call an endpoint rarely says who may read a given record. The two are separate requirements.
**Documented authorization rules are assumed.** Requirements verifying that the rules exist as a document cannot be settled here, and the honest answer is needs-info.
**One trust zone throughout.** Where the model shows no privilege distinction at all, say so — it does not rule the chapter out, and it makes every ruling here rest on the same stated fact.

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
