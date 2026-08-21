# Code You Did Not Write

## When this applies

The model names a dependency, a package registry, a container image, a lockfile, an artifact store, or a component operated by somebody else. Chapter V15 covers secure coding and architecture, and most of what a description reveals about it is the supply chain.

## What to look for

- **Fixed versions, verified before they run.** The requirement is that what runs is what was intended: a resolved lockfile, a pinned image digest, a checked signature. A description naming a public registry without naming a pin is a fair question.
- **The build is part of the application.** A pipeline that resolves dependencies and produces artifacts is where an unverified component enters, so the pipeline's own trust is in scope.
- **Third party covers services, not only libraries.** A payment processor, a gateway somebody else operates, or a model registry an engineer publishes to are all components this system trusts without controlling.
- **Unmaintained is a state, not an opinion.** Whether a component still receives fixes, and within what window, is a requirement the standard states in terms of documented timeframes.
- **Documented architecture.** V15 asks for the security-relevant architecture decisions to be written down. A description that reveals a real architecture and no stated documentation is a ruling on that requirement.
- **Defensive coding.** Where the description names a language or runtime, the chapter's requirements about unsafe constructs apply, though prose will rarely settle them.

## Guardrails

- Analysis knowledge, not evidence. Cite the element or the prose that named the component.
- Rule applicability, never a pass. A named lockfile does not confirm it is enforced at install time.
- How a component is *configured* is V13. Whether it should be trusted at all is here.
