# 23. A vendor SDK is an optional extra

- **Status**: accepted
- **Date**: 2026-09-05
- **Effort**: [#498 — Whether the runtime image takes on
  boto3](https://github.com/mstarks01/work-agent/issues/498), on the Bedrock map
  [#491](https://github.com/mstarks01/work-agent/issues/491)

## Context

Three vendor rows reach their provider through litellm's own HTTP stack, so the
wheel needs no vendor SDK. Bedrock is the first row that breaks that. Every
Bedrock path in litellm imports `boto3`, and neither credential mode avoids it:
the chat path resolves AWS credentials before it reads a bearer token, and the
last branch of that resolution runs `import boto3`.

The offline half of the service does not need the SDK. The model gate, the
conformance matrix and the capability probes read litellm's pinned cost map, and
that map answers for `bedrock` with no AWS package installed.

## Decisions

### A vendor that needs a vendor SDK gets an extra named after the vendor

The SDK is not in `dependencies`. It is in `project.optional-dependencies`, so a
deployment that never selects the vendor never installs it. This is a property
of the vendor rather than a fact about Bedrock: the next vendor whose provider
needs a client library gets the same treatment.

### The registry names the SDK in a table, and the check is a probe

`vendors.py` holds a table keyed by vendor. Each entry names the module to probe
and the extra that supplies it, and a vendor that needs no SDK answers `None`.
`None` rather than an absent key, because `tests/test_vendor_neutrality.py`
finds every vendor-keyed table and requires it to answer for every vendor: a row
that simply never appeared would raise at the first build that selected it,
instead of at the guard built to catch exactly that.
The check runs `importlib.util.find_spec`, never an `import`, for two reasons.
An `import` would make the package's own AST scan
(`tests/test_identity.py`) demand a hard dependency, which contradicts the
decision above. And a probe reports what is installed without loading it.

### The check fires where the credential check fires

`binding.py` builds one adapter per bound tier, and that is where
`Vendor.credential_kwargs` already runs. The SDK check joins it. A tier that
nothing runs on builds no adapter, so it costs no credential and now costs no
SDK either. The alternative — a check at deployment load — would refuse a
process over a tier it never binds.

The first-run diagnostic page reads the same table and reports the module beside
the vendor's variables. A page that reports every variable as set, while the run
still fails, is worse than a page that says nothing.

### The SDK carries an ordinary version range, not an exact pin

`litellm` and `google-adk` carry exact pins because this service reads their
APIs directly. It does not read the vendor SDK's API at all — litellm is the
caller — so the range this project declares is the range litellm declares for
its own use. A litellm bump must re-read that range rather than assume it holds.

### The SDK is not part of the execution identity

`BUILD_DISTRIBUTIONS` names the distributions whose version can change what a
node answers. A vendor SDK authenticates a request and signs it; it does not
shape the prompt or parse the response, which is why the HTTP stack underneath
litellm is absent from that list too.

There is a second reason, and it decides the question on its own.
`build_identity` fails closed on a distribution it cannot find. A vendor SDK
named in that tuple would make every install without the extra unable to produce
an execution identity, which turns the optional extra back into a hard
dependency.

### The offline lane installs the extra, and the missing branch is driven

This reverses what this ADR first said, on a fact the first version missed. The
offline conformance suite builds an adapter for **every** vendor, because that
is how it establishes that the application treats them alike. Without the SDK,
the vendor that needs one is the single vendor that suite cannot cover — which
is the imbalance [#116](https://github.com/mstarks01/work-agent/issues/116)
existed to remove, reappearing in the suite built to remove it.

So the `dev` dependency group names the project's own extra, and one place still
states the version range. The missing-SDK branch is driven directly instead: a
test makes the probe answer the way it would on an image without the library,
which is the same call the gate makes.

## Consequences

An operator who selects such a vendor installs `analysis-service[<vendor>]`. The
bind-time error names the extra, so the instruction is in the failure rather
than only in a guide.

A deployment that installs the wheel without the extra and selects the vendor is
refused at build time. No offline lane runs in that state, so the refusal is
proved by driving the probe rather than by the absence of a package.
