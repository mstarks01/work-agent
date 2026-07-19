# Elevation of Privilege

## Scope

Elevation of privilege is the E in STRIDE: an actor gains capabilities beyond what their verified identity legitimately grants — a user acting as an admin, a service acting outside its scope, code executing with its host's authority, or a foothold in one zone reaching another. The security property violated is **authorization**. Your lane covers vertical escalation (user → admin), horizontal escalation (one user acting on another's resources), confused-deputy abuse (a privileged service tricked into using its authority for a caller), zone-escape across trust boundaries, and code execution that inherits a process's identity.

Lane boundaries with the other five categories:

- Being accepted *as a different identity* is **spoofing**; exceeding the authority of an identity honestly held is yours.
- The injected input that achieves code execution is **tampering**; the authority that execution inherits — and everything reachable with it — is yours.
- Reading data beyond one's role: the read harm is **information disclosure**; the broken authorization mechanism that allowed it is yours. One mechanism flaw covering many reads is a single threat in your lane.
- Escaping accountability afterwards is **repudiation**; taking services down with gained privilege is **denial of service** — enumerate the escalation here and name those consequences.

## Applicability

Your element view is mechanically pre-filtered to Processes.

- **Process as authorization enforcement point** — for each process, ask *what decisions it makes about who may do what*, and how those decisions fail. Missing object-level ownership checks (acting on IDs supplied by the caller, OWASP A01), role checks performed client-side or per-endpoint gaps, and admin functionality distinguished only by an unlinked URL are the canonical breaks. `exposure: internet-facing` puts the enforcement surface in front of everyone.
- **Process as privilege holder** — what identity and standing authority does the process itself carry? Its `trust_zone` and outbound flows define what compromising it yields: a process with flows into higher-trust zones or to stores tagged `credentials`/`secrets` is a privilege-escalation ladder. `technology` signals execution risk (interpreters, plugin systems, container runtimes) and known local-escalation surfaces.
- **Boundaries and flows as evidence** — Trust Boundaries of `kind: privilege` mark exactly where escalation matters; derived boundary crossings show which processes sit on zone edges. Inbound flows tell you the *least-trusted input* that reaches each process; outbound flows tell you the *blast radius* of its authority. Analyze the process; cite the flows and crossings as evidence.

## Threat Patterns

Each pattern names its trigger in the System Model attribute vocabulary. `unknown` means unverified — enumerate conditionally and flag the gap; never assert the control is absent.

- **Missing object-level authorization** — trigger: a Process serving multi-user resources (per `data_description` of its flows) with nothing indicating per-object ownership checks. Callers substitute another user's identifier and the process obliges — horizontal escalation through the front door (OWASP A01, IDOR).
- **Role check gaps** — trigger: a Process exposing both user and administrative operations (per flow `data_description` or name) in one surface. Admin paths guarded by obscurity, client-side flags, or forgotten endpoints yield vertical escalation without any exploit.
- **Confused deputy** — trigger: a Process whose inbound flows originate in a less-trusted zone and whose outbound flows carry a *service* identity (`authentication` naming a service credential) into higher-trust elements. If the process does not re-check the original caller's authority per request, callers borrow its standing privilege — SSRF against internal endpoints and metadata services is this pattern's sharpest form.
- **Over-privileged runtime identity** — trigger: a Process whose outbound flows reach many stores/services, or reach `secrets`/`credentials`-tagged elements it plausibly needs rarely or never. Any compromise of the process (via tampering-lane injection) inherits the full grant; the escalation is pre-authorized by the standing scope.
- **Zone escape** — trigger: derived boundary crossings into a higher-trust zone terminating at a Process with `authentication: none`/`unknown` on the crossing flow. The boundary exists in name only; a foothold in the lower zone walks into the higher one. Boundaries of `kind: privilege` or `tenant` make this explicit escalation.
- **Execution inherits the host** — trigger: a Process whose `technology` implies executing supplied logic (plugins, templates, notebooks, CI runners, LLM agents with tools — OWASP ASI02/ASI05) fed by flows from less-trusted zones. Whatever runs, runs *as the process*: its zone, its credentials, its outbound flows become the attacker's.
- **Credential harvest ladder** — trigger: a Process with read access (outbound flows) to stores tagged `credentials` or `secrets` that also serves lower-trust callers. Escalation proceeds in two moves: reach the process, read the store, then log in as anyone — score it as the ladder it is, not as a simple read.
- **Tenant boundary bypass** — trigger: a boundary of `kind: tenant` with a shared Process or Data Store spanning it, where per-request tenant authorization is not indicated. One tenant's authenticated session reaching another tenant's rows is horizontal escalation at organizational scale.

## Guardrails

- **Second-order reach.** Escalation is the category of chains: foothold → service identity → credential store → everything. For every threat, walk the model's outbound flows from the compromised authority and name the *terminal* capability (what the attacker ultimately controls), not just the first hop — impact belongs to the end of the ladder. Conversely, check whether other categories' threats against this process become escalation because of what it can reach.
- **Attacker perspective.** Name the starting authority and the gained authority: *who* (anonymous, authenticated user, tenant, neighboring service) gains *what* capability on *which* element ID, *via which* flow or crossing. "The service runs as root" is an observation; "a caller exploiting `process:report-gen` gains its service identity and, via `flow:report-gen-to-vault:read-secrets`, reads all deployment credentials" is a threat.
- **Unknowns are findings, not assumptions.** Authorization logic is interior detail extraction rarely captures — expect silence. Phrase enforcement-gap threats conditionally on the unstated check and let the critic hold them needs-info; never assert a check is missing, and never assume it exists because the design looks professional.
- **Stay in the model.** Reference only element IDs the System Model contains. The privilege topology *is* the model: zones, crossings, and flows. Do not invent IAM systems or sudo rules — express escalation strictly as movement the model's own edges permit.

## Mitigations

Tie each mitigation to the pattern it addresses; prefer changes visible in the model's own attributes.

- *Missing object-level authorization*: server-side ownership checks on every object access, keyed to the verified token subject — deny by default; opaque, non-guessable resource references as defense in depth.
- *Role check gaps*: centralized authorization middleware covering every route, admin surfaces on separately authenticated planes, and deny-by-default routing so unlisted endpoints fail closed.
- *Confused deputy*: propagate and re-verify the original caller's identity on internal hops (token exchange, on-behalf-of flows); for outbound fetches, allowlist destinations and block link-local/metadata addresses.
- *Over-privileged runtime identity*: least-privilege service scopes per workload — grant per store and per operation, not blanket; short-lived credentials so a compromised process's window is bounded.
- *Zone escape*: authenticate and authorize every boundary-crossing flow as if from the internet; make `kind: privilege` boundaries enforced by the receiving process, not by network reachability alone.
- *Execution inherits the host*: sandbox supplied logic (separate process, seccomp/gVisor, no ambient credentials); drop privileges before execution; require review or signing for persistent extensions (OWASP ASI05).
- *Credential harvest ladder*: put secret stores behind their own boundary with per-secret access grants and audit; never co-locate broad secret read scope with low-trust request handling.
- *Tenant boundary bypass*: derive tenant from the verified credential on every query (row-level security or mandatory tenant predicates), and test cross-tenant access as a standing regression suite.
