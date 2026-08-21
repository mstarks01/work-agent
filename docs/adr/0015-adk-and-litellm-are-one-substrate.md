# 15. ADK and LiteLLM are one substrate, and neutrality names the provider axis

- **Status**: accepted
- **Date**: 2026-08-21
- **Relates to**: [ADR 0003](0003-no-privileged-vendor.md), whose audit this one
  states the boundary of. 0003 is correct about the axis it names and silent
  about the layer beneath it.

## Context

A vendor-neutrality audit on 2026-08-21 re-ran ADR 0003 against the code. The
model-provider half held. `src/stride_service/vendors.py` is the only module
that names a vendor in executable code, the two temperature rules in
`binding.py` key on the model family, and `tests/test_conformance.py` checks the
reference table against the registry. No default vendor ships.

The audit also found a question ADR 0003 never asked. That ADR classified every
occurrence of `vertex`, `gemini`, `google`, `ping`, `flash`, `pro` and `RS256`.
Each of those names a **model provider** or an **identity provider**. Neither
list can find a dependency that is neutral about providers and singular about
everything else.

Two dependencies are exactly that:

| Pin | What it supplies |
| --- | --- |
| `google-adk==2.5.0` | the orchestration graph, the agent, the runner, the session store, the event type, and the model adapter |
| `litellm==1.97.0` | the provider routing under that adapter, **and** the build-time supported-param gate |

The ADK surface is wider than the model binding, and most of it has nothing to
do with which vendor answers. `graph.py` imports `START`, `FunctionNode`,
`JoinNode` and `Workflow` from `google.adk.workflow`, and builds the whole
pipeline topology out of them — `FunctionNode` alone appears 31 times in `src/`.
`execution.py` drives that graph through `google.adk.Runner` and
`google.adk.apps.App`. `LlmAgent`, `BaseLlm`, `Event` and `BaseSessionService`
are ADK types on interfaces this service defines. Swap the vendor and none of
that moves. Swap ADK and all of it does.

LiteLLM is load-bearing twice, and the second use is the harder one.
`litellm.utils.get_optional_params` **is** the build-time gate in
`model_gate.py`. It is not a documented public API, which is why `pyproject.toml`
pins it exactly and says a bump must re-run the probe in
`tests/test_model_gate.py`.

Neither dependency is an accident. Ticket #4 established that `LiteLlm` behind
`BaseLlm` is the one path that preserves the served-build readback, because
ADK's native Claude class silently breaks `model_version` — and that readback is
what the whole reproducibility guarantee rests on. Ticket #8 extended the result
to every target vendor and found no vendor that forces a native adapter. The
sole-adapter rule is therefore a *finding*, not a preference.

So the position is settled and unstated. That combination is what this record
fixes. ADR 0003 reads as a closed audit, so the next reader takes the whole
question as answered.

## Decision

### Vendor neutrality names the model-provider axis, and says so

This service is neutral about **which provider serves a model**. It is not
neutral about ADK, and it is not neutral about LiteLLM. Those two are a single
accepted substrate, and no claim anywhere in this repository should imply
otherwise.

The claim keeps the definition ADR 0003 gave it: equivalent application
behaviour given equivalent provider capabilities. That definition already
presumes one application. Naming the substrate makes the presumption visible.

### No abstraction layer over either dependency

No port, no adapter interface, no anti-corruption layer. ADK types appear
directly on this service's own interfaces, and that stays.

Two reasons, and the second is the stronger one. The first is ordinary: a
wrapper around a framework you have exactly one of costs real code and buys an
option nobody has priced.

The second is precedent from this repository. Ticket #6 gave `Vendor` a
`supported: frozenset[str]` field that mirrored what LiteLLM knows about
sampling params. Ticket #12 falsified it within a week: the answer is a function
of `(vendor, model)`, `vertex_ai/` is not one provider, and the mirror was too
loose for exactly the mid-job raise it existed to prevent. The replacement was
**nothing** — the check became a call. A wrapper over LiteLLM is that same
mistake at a larger scale, and it fails the same way, which is silently.

### The sole-adapter rule is a test, not a sentence

`tests/test_conformance.py::test_no_vendor_reaches_its_provider_by_a_different_class`
builds the tier adapters for all three vendors and compares the class ancestry
behind the per-call retry subclass. A native path reintroduced for one vendor
fails there.

This matters because ADK argues the other way on every run. It emits
`[GEMINI_VIA_LITELLM]` on each Vertex bind and recommends its native Gemini
client. Taking that advice is what would create the asymmetry ADR 0003 removed,
and the recommendation arrives from inside the dependency rather than from a
person. Prose cannot hold a line under that kind of pressure.

### A version bump on either pin is a re-probe

Already true of `litellm` and stated in `pyproject.toml`. It is now stated of
`google-adk` for the same reason: the ADK facts this service depends on are
behavioural rather than documented. Ticket #6 rests on ADK 2.5.0 not mapping a
node's `thinking_config` into LiteLLM reasoning params, which is why `seed`,
reasoning and the retry budget ride the `LiteLlm` constructor. A minor bump can
move that.

### What reopens this

Named triggers, so the next reader does not have to judge:

- ADK removes or reshapes `google.adk.workflow`, which is the deepest coupling
  and the one furthest from anything about vendors.
- LiteLLM changes `get_optional_params` such that the gate cannot ask its
  question. The gate is the reason the pin is exact.
- A target vendor appears that LiteLLM does not route. Ticket #8 found none, and
  it is the finding most likely to expire.
- Someone needs to run this service without a Google-published dependency in the
  tree. That is a procurement fact rather than a technical one, and no ADR can
  pre-decide it.

Periodic review is deliberately **not** a trigger. It produces a re-audit with
no new fact in it, which is the thing ADR 0003 was written to stop.

## Consequences

**ADR 0003 stays correct and stops reading as complete.** Its audit answered the
provider question well. It is now explicitly one axis of two.

**The blessed fingerprint carries a LiteLLM string, permanently.**
`Vendor.prefix` is LiteLLM's router token — `vertex_ai/`, not `vertex` — and
`join_served` prefixes the served build with it before the hash. So every
blessed fingerprint embeds a naming convention LiteLLM chose. Replacing LiteLLM
would re-baseline the whole manifest.

*Considered and rejected: hash `Vendor.name` instead, which this repo owns.* It
is a one-line change and it would cost nothing today, because
`config/blessed-fingerprints.toml` ships empty. The "last cheap moment"
argument that carried #15's cutover does **not** apply here, and that is the
whole reason to decline. The cost of a swap is a re-blessing, and a re-blessing
is only payable once a blessing exists. The moment therefore stays cheap for
exactly as long as nobody has run a sanctioned sweep — and the run that ends
that is also the run that gives someone a reason to care. Deferral is free here
in a way it genuinely was not there.

**The research that settled the substrate is reachable from one clone.** Five
commits hold it, and none is an ancestor of `origin/main` or on any remote ref:

| Commit | Document |
| --- | --- |
| `e3ffd53` | `docs/research/adk-nongemini-adapters.md` (#4) |
| `42e2192` | `docs/research/litellm-sole-adapter.md` (#8) |
| `3a1d711` | `docs/research/vendor-sampling-support.md` (#12) |
| `24047f0` | the `get_optional_params` gate probe (#13) |
| `47d3c82` | the `reasoning_effort` surface probe (#15) |

They survive today only because this working copy has not been garbage
collected. Recording the SHAs here makes this ADR the recovery path, which is
weaker than the documents themselves being on `main`. Restoring them is a
separate change and is not made here.

**Nothing in the code changes.** This ADR ratifies what `binding.py`,
`model_gate.py` and `tests/test_conformance.py` already do. The decidable half
was already decidable, which is why the record was overdue rather than urgent.
