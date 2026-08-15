# Privilege and Tenant Transitions

## When this applies

A flow crosses a boundary that separates privilege levels or parties rather than network locations, and what enforces the transition is unstated. A privilege boundary counts on the way in; a tenant boundary counts both ways, because leaving one is a party we do not control reaching a zone we do.

## What to look for

- **Authentication is not authorization.** Knowing who is calling says nothing about what they may do. At a privilege transition the interesting control is the check that runs *after* identity is established, and models frequently state the first and omit the second.
- **Where the decision is made.** A check performed by the caller, or in a user interface, or by a client-side role flag, is a suggestion. The enforcement point has to sit on the side that holds the authority.
- **Ambient authority.** A component that holds broad standing permission — an admin service account, a cluster role, a database superuser, a cross-tenant key — grants everything it can do to anything that can persuade it to act. The confused deputy is this pattern: a low-privileged caller supplying the target of a high-privileged action.
- **Tenant identifiers as parameters.** Where a tenant, organisation or account is carried in a request field, the question is what binds it to the authenticated principal. Unbound, it is a horizontal escalation with a well-formed request.
- **The transition's own surface.** Impersonation and support-access features, break-glass roles, delegation and "act as" flows are legitimate privilege transitions whose controls are worth naming explicitly.

## Guardrails

- Analysis knowledge, not evidence. What enforces a transition in this system is what the submitted material says, or `unknown`.
- Name what the caller would command. An elevation finding that stops at "the boundary is crossed" is a restatement of the model; the finding is the specific authority obtained and the action it permits.
- Being accepted as another identity is spoofing. Acting beyond the authority of an identity you legitimately hold is this lane.
