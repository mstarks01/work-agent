# Session Management (V7)

## Scope

Chapter V7 of ASVS 5.0: the state that carries identity between requests. Your lane covers session token generation and entropy, binding a session to its holder, timeout and absolute lifetime, termination on logout and on credential change, and re-authentication for sensitive operations.

Chapter boundaries: establishing identity in the first place is chapter V6. A self-contained token's internal claims are chapter V9. A cookie's browser attributes are chapter V3. Your subject is the session as a thing that exists, expires and can be revoked.

## Applicability

**This chapter needs a session.** ASVS names stateless APIs as its own example of a system where these requirements do not apply, and that exclusion is worth taking seriously: a machine-to-machine surface authenticating each request independently holds no session to manage.

Read the model for a session: a `DataFlow.authentication` naming a session cookie or a session token, a store holding session state, an interactive human entity. Where the input describes per-request credentials and nothing else, rule the chapter out and name that.

### The requirements of this chapter

19 requirements across 6 sections: 6 at level 1, 12 at level 2, 1 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V7.1 Session Management Documentation

- **V7.1.1** (L2) — Verify that the user's session inactivity timeout and absolute maximum session lifetime are documented, are appropriate in combination with other controls, and that the documentation includes justification for any deviations from NIST SP 800-63B re-authentication requirements.
- **V7.1.2** (L2) — Verify that the documentation defines how many concurrent (parallel) sessions are allowed for one account as well as the intended behaviors and actions to be taken when the maximum number of active sessions is reached.
- **V7.1.3** (L2) — Verify that all systems that create and manage user sessions as part of a federated identity management ecosystem (such as SSO systems) are documented along with controls to coordinate session lifetimes, termination, and any other conditions that require re-authentication.

#### V7.2 Fundamental Session Management Security

- **V7.2.1** (L1) — Verify that the application performs all session token verification using a trusted, backend service.
- **V7.2.2** (L1) — Verify that the application uses either self-contained or reference tokens that are dynamically generated for session management, i.e. not using static API secrets and keys.
- **V7.2.3** (L1) — Verify that if reference tokens are used to represent user sessions, they are unique and generated using a cryptographically secure pseudo-random number generator (CSPRNG) and possess at least 128 bits of entropy.
- **V7.2.4** (L1) — Verify that the application generates a new session token on user authentication, including re-authentication, and terminates the current session token.

#### V7.3 Session Timeout

- **V7.3.1** (L2) — Verify that there is an inactivity timeout such that re-authentication is enforced according to risk analysis and documented security decisions.
- **V7.3.2** (L2) — Verify that there is an absolute maximum session lifetime such that re-authentication is enforced according to risk analysis and documented security decisions.

#### V7.4 Session Termination

- **V7.4.1** (L1) — Verify that when session termination is triggered (such as logout or expiration), the application disallows any further use of the session. For reference tokens or stateful sessions, this means invalidating the session data at the application backend. Applications using self-contained tokens will need a solution such as maintaining a list of terminated tokens, disallowing tokens produced before a per-user date and time or rotating a per-user signing key.
- **V7.4.2** (L1) — Verify that the application terminates all active sessions when a user account is disabled or deleted (such as an employee leaving the company).
- **V7.4.3** (L2) — Verify that the application gives the option to terminate all other active sessions after a successful change or removal of any authentication factor (including password change via reset or recovery and, if present, an MFA settings update).
- **V7.4.4** (L2) — Verify that all pages that require authentication have easy and visible access to logout functionality.
- **V7.4.5** (L2) — Verify that application administrators are able to terminate active sessions for an individual user or for all users.

#### V7.5 Defenses Against Session Abuse

- **V7.5.1** (L2) — Verify that the application requires full re-authentication before allowing modifications to sensitive account attributes which may affect authentication such as email address, phone number, MFA configuration, or other information used in account recovery.
- **V7.5.2** (L2) — Verify that users are able to view and (having authenticated again with at least one factor) terminate any or all currently active sessions.
- **V7.5.3** (L3) — Verify that the application requires further authentication with at least one factor or secondary verification before performing highly sensitive transactions or operations.

#### V7.6 Federated Re-authentication

- **V7.6.1** (L2) — Verify that session lifetime and termination between Relying Parties (RPs) and Identity Providers (IdPs) behave as documented, requiring re-authentication as necessary such as when the maximum time between IdP authentication events is reached.
- **V7.6.2** (L2) — Verify that creation of a session requires either the user's consent or an explicit action, preventing the creation of new application sessions without user interaction.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**A session exists and its lifetime is not stated.** Idle timeout and absolute lifetime are separate requirements and both are usually open.
**Termination is described for logout alone.** Whether a session survives a password change or an administrative disable is its own requirement, and prose rarely reaches it.
**The token's generation is unmentioned.** Entropy and generation method are requirements the input can almost never settle. Say so rather than assuming a framework default.
**Stateless by design.** Where every flow carries its own credential, rule the session requirements out and name the flows that decided it.

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
