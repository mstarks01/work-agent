# Controls the Browser Enforces on Your Behalf

## When this applies

The model shows a process serving a browser, or anything that sets a cookie. Chapter V3 covers the security a response header or a cookie attribute buys, which is enforcement the application asks the browser to perform.

## What to look for

- **Each header is its own requirement.** Content Security Policy, frame ancestors, referrer policy, content-type options and permissions policy are separate rulings. "We set security headers" answers none of them individually.
- **Cookie attributes are per cookie.** `Secure`, `HttpOnly`, `SameSite`, `Path`, `Domain` and the host prefix each carry their own requirement, and a system with a session cookie and an analytics cookie may treat them differently. Name the cookie the ruling is about.
- **A browser-served response is the trigger.** A JSON API that a browser calls still returns responses a browser interprets, so the content-type and sniffing requirements apply to it too.
- **Frontend frameworks change the shape, not the requirement.** A single-page application still needs the policy; where it renders does not remove the chapter.
- **A described login page implies a session cookie.** The model naming authentication and a browser is usually enough to put the cookie requirements in scope, even where no cookie is named.

## Guardrails

- Analysis knowledge, not evidence. The ruling rests on the model's own process, flow or stated control.
- Rule applicability, never a pass. A named header does not confirm its value is correct, and its value is not in the material.
- Keep V3 apart from V7. How a cookie is *carried and protected* is here; how the session it names is *created, bound and ended* is session management.
