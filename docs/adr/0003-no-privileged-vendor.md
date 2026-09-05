# 3. No privileged model vendor or identity provider

- **Status**: accepted
- **Date**: 2026-08-08
- **Effort**: [#116 — remove remaining Vertex/Gemini/Ping bias and establish
  vendor-neutral conformance](https://github.com/mstarks01/work-agent/issues/116)

## Context

This service was built around Vertex, Gemini and Ping. The runtime stopped being
built that way over several cutovers — one adapter for every vendor (#8/#9),
vendor-neutral tier names (#5/#9/#15), generic OIDC — but *bias* and
*implementation* are different things, and the issue asked which of the original
assumptions were still load-bearing.

The audit is recorded here rather than in a PR comment because three of its
findings are that a claim was **already false**, and a finding of "this was
looked for and is not there" is worth exactly as much as a fix — it is the thing
that stops the same audit being run again next quarter.

## The audit

Every occurrence of `vertex`, `gemini`, `google`, `ping`, `flash`, `pro` and
`RS256` outside `.venv/` and `.wayfinder/`, classified.

### Required provider implementation — kept

`src/analysis_service/vendors.py` is the only module that names vendors, and it
holds three facts per vendor that nothing else can supply: the LiteLLM router
prefix, the credential modes the vendor allows, and the pinned-form rule.
`ANALYSIS_VERTEX_PROJECT` and `ANALYSIS_VERTEX_LOCATION` address a Vertex
deployment; they are not credentials, and the registry names no credential for
Vertex at all. All of this is implementation, not bias.

### Historical bias — fixed here

| Where | Was | Now |
| --- | --- | --- |
| `config/model_tiers.toml` | the one worked example was a Vertex/Gemini pair | a `<vertex\|anthropic\|openai>` template, then all three vendors listed alphabetically at equal detail |
| `docs/First-Run.md` | Vertex had its own section; Anthropic and OpenAI shared one | three sections, alphabetical, equal detail, each with a complete config block |
| `docs/HTTP-API.md` | Ping led the IdP table | alphabetical, and stated to be illustrative rather than exhaustive |
| `tests/test_auth.py`, `tests/test_pipeline*.py` | fixtures used `ping.example.com` and `ping\|user-1` | `idp.example.com`, `idp\|user-1` |
| `src/analysis_service/auth.py` | RS256 was unconfigurable — see below | `ANALYSIS_OIDC_ALGORITHMS`, over an allowlist |

### Already resolved — no change needed

Three of the issue's claims did not survive the audit, and saying so is part of
the record:

- **ADK does not privilege Gemini.** Every model reaches its provider through
  one `LiteLlm`; there is no native Gemini client anywhere in `src/`. ADK emits a
  warning that Gemini would be better served natively, and taking that advice is
  what would create the asymmetry. Retry, sampling resolution, structured-output
  handling, and fingerprinting are single code paths with no vendor branch.
- **No Ping-specific assumption reaches the implementation.** `auth.py` is
  spelled in issuer / audience / JWKS / claims / algorithm and nothing else. The
  only hits were fixture hostnames and one documentation row.
- **`flash` / `pro` are gone.** Retired in the v3 tier cutover; `base` / `strong`
  are a capability axis.

### Test and evaluation bias — closed by removal (2026-08-20)

The eval judge was `vertex`/`gemini-2.5-pro` and its ≥90% agreement check
never ran, so the measurement system's own vendor dependence was unmeasured.
On 2026-08-20 the judge was removed instead of re-vendored: claim matching is
the identity rule in `evals/harness/identity.py` (92.5% against the recorded
labels, no provider call), and the standing of an unmatched finding comes from
the human vote ledger. No vendor participates in any scored number, so the
question this section tracked no longer has a subject.

## Decisions

### The conformance suite is credential-free, and that is why it is fair

The issue's stated problem was an assurance imbalance: Vertex exercised by CI,
other vendors exercised manually. The premise turned out to be wrong in a way
that matters more than the claim — Workload Identity Federation is unprovisioned,
so the Vertex lane *skips* and has never run. Neither the API-key lane nor the
Vertex lane has ever produced a result. The imbalance was in
intent; the assurance was zero across the board.

A live smoke suite per vendor was therefore the wrong first move: it would have
added three lanes that all skip, to fix an imbalance between two lanes that
already both skip. What was actually missing was coverage that *can* run.

So `analysis_service.conformance` probes the pinned `litellm`'s local model-cost
map, and `tests/test_conformance.py` hands `build_tier_adapters` a synthetic
environment — the registry checks that a credential was *declared*, never that
it authenticates. Both run for all three vendors in the offline lane on every
pull request, with no key and no egress.

The honest limit, stated wherever the suite is described: this proves what each
provider would be asked for and that the application treats the three
identically. It is **not** evidence that any vendor has served a request. Live
coverage remains unprovisioned, and the conformance matrix must never be quoted
as though it closed that gap.

It caught a defect on its first run. `docs/First-Run.md` and
`docs/Configuration.md` both recommended `claude-opus-5` on the `strong` tier;
under the `config/sampling.toml` shipped at the time, that pair was refused at
startup by two independent gates — Claude 4.7+ does not accept `temperature`,
which that file then pinned, and the model would get *emulated* rather than
native schema constraint. The sampling file no longer pins a temperature, so
the first gate is now inert by default and the second still refuses the pair. The documented
first-run configuration for one of three vendors could not start the service.
`.github/workflows/evals-live-api-key.yml` had the constraint right, in a comment,
which is precisely the kind of knowledge a suite exists to hold instead.

### Capability differences are reported as three states, not two

`Capability` is `SUPPORTED` / `UNSUPPORTED` / `UNKNOWN`, and the third value is
the reason the module exists rather than a completeness gesture.

Every gate in `binding.py` is a *raise*, and for an unmapped model LiteLLM
answers out of the provider's base config rather than declining to answer. That
fallback is right for a gate — refusing to run a model the map has not caught up
with is worse than letting it through — and becomes a lie in a report, where
"this provider rejects it" and "nothing here knows" arrive as the same
non-raise. A matrix that rendered the second as the first would invent a fact.

The corollary is a non-goal held deliberately: providers are **not** made to look
alike. Anthropic rejects `seed`; `gpt-4o` rejects `reasoning_effort`; output
ceilings differ by eight times across the profiled pairs. Vendor neutrality is
*equivalent application behaviour given equivalent provider capabilities* — the
same build-time refusal naming the same tier, the same fingerprint rule, the same
report schema — and it is not capability parity.

### RS256 was a historical assumption; it is now a configured allowlist

The audit's question was whether RS256-only was policy, limitation, or
inheritance. It was inheritance with a twist: `OidcSettings.algorithms` existed
as a field and `from_env` never read it, so the knob was present, documented by
its own existence, and unreachable. A standards-compliant IdP signing ES256 could
not be pointed at this service at all.

`ANALYSIS_OIDC_ALGORITHMS` now configures it, and the security content is entirely
in what it refuses. The allowlist admits the asymmetric families
(`RS*`, `PS*`, `ES*`, `EdDSA`) and configuration selects from it rather than
extending it, because two candidates must never be reachable:

- **`none`**, which makes every token forgeable.
- **`HS*`**, which is the key-confusion attack: JWKS keys are *public*, so a
  verifier accepting HMAC beside asymmetric algorithms will verify a token an
  attacker signed with the public key as the shared secret.

The list is also not read from the IdP's discovery document, though it is
published there. Letting the party being verified declare how it is verified
inverts the trust relationship the check exists to establish.

RS256 stays the default, so a deployment that sets nothing verifies exactly what
it verified before.

### "Gemini support" means Vertex-hosted Gemini. The Developer API is out of scope

Asked and answered rather than left ambiguous:

```text
Gemini support = Gemini through Vertex AI
```

The Gemini Developer API (`generativelanguage.googleapis.com`, AI Studio keys) is
**not** supported and is not planned here. It is a different endpoint with a
different credential model and a different LiteLLM router prefix (`gemini/`
rather than `vertex_ai/`), so adding it would be a fourth entry in the vendor
registry — a new `(vendor, model)` binding — and never a flag on the Vertex one.
Treating the two as interchangeable is the specific mistake this decision
forecloses: they would collide in the fingerprint, where the vendor prefix is
what keeps two hosts of the same model from certifying each other.

Nothing about this is a judgement on the Developer API. It is that a provider
this repository cannot exercise should not be listed as supported, and the live
lanes that would exercise it do not run.

## Consequences

- The offline suite now covers all three vendors equally, on every pull request.
  `.github/workflows/ci.yml` renders the capability matrix into the job summary,
  so a capability that moves under a `litellm` bump is visible in the run that
  bumped it.
- **Unexercised coverage is reported as unexercised.** The CI summary states
  plainly that no live lane has run, rather than letting a green offline check
  read as provider validation. This reuses *unexercised* in the sense
  `CONTEXT.md` already gives it for certification: a thing the run declared and
  did not exercise, which is not the same as a thing that passed.
- A live lane pinned to a model the offline matrix does not profile now fails the
  offline suite, so the two cannot drift apart silently.
- `docs/First-Run.md` no longer has a copyable configuration that cannot start.
- **Closed on 2026-08-20 by removal rather than measurement**: the eval judge
  is gone. Claim matching is the identity rule, offline and vendor-free, gated
  by `calibrate` on the same 90% bar against the same labels. Whether an
  unmatched finding is real is answered by a human vote in
  `evals/harness/ledger.py`, keyed by the finding's fingerprint so one vote
  survives every future run. The caveat this list used to carry — that no
  "model A beats model B" conclusion be quoted without noting the judge's
  vendor — is retired with it: no vendor participates in any scored number.
- **This audit covers one axis of two.** Every term it classified names a model
  provider or an identity provider, so no term could find a dependency that is
  neutral about providers and singular about everything else. `google-adk` and
  `litellm` are both. See [ADR 0015](0015-adk-and-litellm-are-one-substrate.md),
  which states that boundary and does not move it.
