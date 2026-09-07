# The Requirement That Asks for a Document

## Pattern

A model shows a file upload into a processing service. The description covers what the system does with a file in some detail, and says nothing about which file types are permitted or who decided. The level's file-handling requirements include the documentation requirement in the chapter's first section.

## Considered

Skipping the documentation requirement, on the grounds that it asks about a process rather than about the system, and that no description would ever answer it.

## Ruling

Accepted, as **needs-info** — the documentation requirements are requirements, and this one applies.

## Why

ASVS 5.0 added documentation requirements deliberately, and they are not filler. Where a rule is too application-specific for the standard to state — which file types are allowed, what the business rules are, which data is sensitive — the standard requires the organization to write its own decision down, and pairs it with an implementation requirement that the decision be enforced.

Two things follow. First, the documentation requirement and its implementation partner are separate rulings, and verifying one says nothing about the other. Second, "the description does not say" is not "no document exists". A submitter describing a running system rarely attaches its documentation, so the ruling is that the requirement applies and this input does not settle it, and the document itself would. Only a submitter who states that nothing was written down settles it the other way.

Skipping these because they feel procedural would drop a whole section from every chapter that carries one, and the level's coverage check would then have to list them as considered-and-unraised, which says less than the ruling does.

## What decided it

The upload element in the model, which put the chapter in scope, together with the absence of any stated rule about permitted types. That absence is what makes the ruling open rather than settled; it is not evidence that no rule was ever written.
