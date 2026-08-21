# Who May Reach Which Function and Which Record

## When this applies

The model distinguishes one kind of caller from another — an administrator, a tenant, a back-office user, a moderator — or names a role, a permission or an entitlement. Chapter V8 asks what decides access, and it is a different question from who the caller is.

## What to look for

- **Two questions, not one.** Function-level access asks whether this caller may call this operation. Data-specific access asks whether they may reach *this record*. A system can enforce the first perfectly and still return any customer's order to anyone who guesses an identifier.
- **The identifier is the usual failure.** A sequential or guessable key in a URL or payload, with the decision made from the key alone, is the shape the standard names. The remedy is a check against the caller's own permissions, not an unguessable identifier.
- **Enforced at the trusted end.** A role carried in a token the client holds, or a menu that simply hides an option, is not enforcement. The requirement is that the server decides.
- **Least privilege applies to the application too.** A single shared database account with full read and write is an access-control fact about the system, not only an operational one.
- **Documentation first.** V8 opens by asking that the access-control rules be written down, including how attributes of the caller and the resource combine. A system with obvious role distinctions and no stated rules is a ruling on that requirement.

## Guardrails

- Analysis knowledge, not evidence. Cite the entity, the flow or the prose that named the role.
- Rule applicability, never a pass. A named role model does not confirm it is enforced, and enforcement is not in the material.
- Proving who the caller is belongs to V6. This chapter starts once identity is settled.
