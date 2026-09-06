"""The vendor registry: the per-provider facts nothing else will carry.

Every model reaches the graph through ADK's ``LiteLlm``, so what varies per
provider is not how to call it. It is three facts the adapter cannot supply:

* the router prefix LiteLLM dispatches on — ``vertex_ai/``, ``anthropic/``,
  ``openai/``, ``bedrock/`` or ``gemini/`` — which is also the vendor half of
  an **Execution Identity** fingerprint;
* the credential modes a vendor allows, which :data:`CREDENTIAL_MODES` holds and
  a deployment declares from. Vertex admits no raw-API-key path under any
  adapter (``BerriAI/litellm#21036``), so ``vertex + api_key`` is
  unrepresentable rather than validated against;
* the floating-form rule for model identifiers, which differs by model family
  rather than by vendor. Claude carries a canonical identifier of its own shape,
  and the vendors that serve it spell that shape two ways — bare, and behind a
  region scope and a family segment — so the rule is keyed by vendor and the
  shapes it is built from are written down once;
* the client library the vendor's provider needs in the image, which
  :data:`VENDOR_SDKS` holds. A vendor whose provider signs its own requests
  needs one, and an optional extra is what supplies it (ADR 0023).

The per-``(vendor, model)`` sampling support set is deliberately not here.
``vertex_ai/`` is not one provider, and the real answer lives in LiteLLM's own
config classes, so the check is a call to ``litellm`` at build time; see
:mod:`analysis_service.model_gate`.

Reasoning effort is likewise not per-vendor data. One uniform
``reasoning_effort`` surface reaches every vendor, so the kwarg is a module
constant rather than a registry field whose value is the same everywhere.

**The mechanism is declared, and the material may be discovered.** A deployment
states its credential mode in config; only then may a vendor's SDK resolve an
identity from its own chain. That is the rule the whole credential half of this
module implements, and :class:`CredentialMode` records why it has to be
enforced here rather than in the adapter.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from analysis_service.errors import ConfigError

VendorName = Literal["vertex", "anthropic", "openai", "bedrock", "gemini"]
VENDOR_NAMES: tuple[VendorName, ...] = (
    "vertex",
    "anthropic",
    "openai",
    "bedrock",
    "gemini",
)

#: How much a vendor's *served* build identifier is worth as evidence.
#:
#: ``provider_reported`` means the translator read the build's name out of the
#: response body, so the provider named what answered. ``requested_echo`` means
#: the translator filled it from the request, so the served half of an
#: **Execution Identity** repeats the requested half and adds nothing.
#:
#: Two values and not three. The reader asks one question — does the served
#: build add evidence the requested build did not — and an echo answers no
#: whatever the reason for it. A Gemini response body carries ``modelVersion``
#: and the pinned translator never reads it; a Bedrock Converse response carries
#: no model identifier at all. Either way the answer is the same.
ServedTrust = Literal["provider_reported", "requested_echo"]

# The one reasoning knob, uniform across vendors: LiteLLM maps it to adaptive
# ``thinking`` plus ``output_config.effort`` on Anthropic (identically via
# Vertex), to ``thinkingConfig`` on Gemini, and passes it through on OpenAI
# o-series. Anthropic's half was ``budget_tokens`` before Claude 4.6 and
# ``output_config.effort`` from 4.6 on; LiteLLM reads the model string and picks
# the half, so this service names the knob once and runs either generation.
REASONING_KWARG = "reasoning_effort"

_API_KEY_TEMPLATE = "ANALYSIS_{vendor}_API_KEY"

# Vertex needs a project and a location to address; they are ordinary config,
# not credentials, but the build cannot construct a working client without them.
VERTEX_PROJECT_VAR = "ANALYSIS_VERTEX_PROJECT"
VERTEX_LOCATION_VAR = "ANALYSIS_VERTEX_LOCATION"

# Bedrock addresses a region rather than a project. Required under both of its
# credential modes and not a credential under either, so it is reported as a
# variable an operator must set and never redacted out of provider error text.
#
# The name is this service's own, and litellm's fallback names are deliberately
# not read: litellm falls back to ``AWS_REGION_NAME`` rather than to AWS's
# conventional ``AWS_REGION``, to ``AWS_PROFILE_NAME`` rather than
# ``AWS_PROFILE``, and to ``AWS_BEARER_TOKEN_BEDROCK`` for a bearer token, which
# AWS tooling sets for its own reasons. A registry that declared that last name
# would let a token nobody chose authenticate a run.
BEDROCK_REGION_VAR = "ANALYSIS_BEDROCK_REGION"


class ProviderAuthError(ConfigError):
    """Outbound provider credentials are missing or unusable.

    Distinct from :class:`analysis_service.auth.AuthConfigError`, which is about
    *inbound* bearer auth on the HTTP surface. Both fail closed at build time;
    conflating them would let one subsystem's misconfiguration read as the
    other's.

    Messages name the environment variable, never its value (OWASP A09): a key
    echoed into a log or a problem+json body has leaked.
    """


class CredentialMode(StrEnum):
    """How a vendor is authenticated.

    **The mechanism is declared, and the material may be discovered.** A
    deployment states its credential mode in config. Only then may a vendor's
    SDK resolve an identity from its own chain. A stray ``ANTHROPIC_API_KEY``
    in the process environment still authenticates nothing, because
    :meth:`Vendor._require` raises before the adapter is built — LiteLLM cannot
    be told to refuse its own chain, and no parameter turns it off, so the
    refusal has to happen here or not at all.

    ``IAM`` means the platform supplies the identity: the deployment passes no
    credential material, and the vendor's SDK resolves one from the environment
    it runs in. A Google product name does not belong in a vendor-neutral
    registry, so this is spelled by what it does rather than by whose it is.
    """

    API_KEY = "api_key"
    IAM = "iam"


#: Which modes each vendor allows, keyed by vendor. A missing key raises, which
#: is the point: a table nobody can be silent in, rather than a default that
#: answers for a vendor its author never considered.
#:
#: Bedrock is the first vendor with a choice, so a deployment that selects it
#: declares which mode it uses. The loader's rules are what carry that: a
#: ``[credentials]`` key for a single-mode vendor is an error, and a missing key
#: for a multi-mode vendor is an error. Both read this table.
CREDENTIAL_MODES: dict[VendorName, tuple[CredentialMode, ...]] = {
    "vertex": (CredentialMode.IAM,),
    "anthropic": (CredentialMode.API_KEY,),
    "openai": (CredentialMode.API_KEY,),
    "bedrock": (CredentialMode.API_KEY, CredentialMode.IAM),
    # The Gemini Developer API takes a key and nothing else. It is a different
    # provider from ``vertex`` rather than a second mode on it:
    # ``get_llm_provider`` resolves ``gemini/`` and ``vertex_ai/`` to two
    # providers, and Vertex admits no key under any adapter.
    "gemini": (CredentialMode.API_KEY,),
}


@dataclass(frozen=True)
class _CredentialVar:
    """One environment variable a ``(vendor, mode)`` pair reads.

    ``secret`` is the field that separates this table's two readers.
    :attr:`Vendor.required_env_vars` reports every entry, because an operator
    has to set every one. :meth:`Vendor.secret_env_vars` reports only the
    secret ones, because that is what must never survive into a job summary.
    A region is required and is not a secret, and one list answering both
    questions gave the wrong answer to the second.
    """

    kwarg: str
    var: str
    secret: bool


def _api_key_var(vendor: VendorName) -> str:
    """The env var holding one vendor's API key."""
    return _API_KEY_TEMPLATE.format(vendor=vendor.upper())


#: The ``(LiteLlm kwarg, env var, secret)`` entries each ``(vendor, mode)`` pair
#: authenticates and addresses with. A table rather than a branch on the mode:
#: a branch answers for the vendors its author had in front of them, and a
#: missing key here raises instead of falling through to somebody else's shape.
#: ``tests/test_vendors.py`` checks this table's keys against
#: :data:`CREDENTIAL_MODES`, because a table nobody compares to its registry
#: fails as quietly as the branch it replaced.
#:
#: Vertex under ``IAM`` passes a project and a location and **no credential**.
#: ``vertex_credentials`` is omitted so that ``google.auth.default()`` runs and
#: resolves whatever identity the platform supplies — a Workload Identity
#: binding, a metadata server, or an operator's own
#: ``GOOGLE_APPLICATION_CREDENTIALS``, through ADC's chain rather than through
#: anything this registry names.
_CREDENTIAL_VARS: dict[
    tuple[VendorName, CredentialMode], tuple[_CredentialVar, ...]
] = {
    ("vertex", CredentialMode.IAM): (
        _CredentialVar("vertex_project", VERTEX_PROJECT_VAR, secret=False),
        _CredentialVar("vertex_location", VERTEX_LOCATION_VAR, secret=False),
    ),
    ("anthropic", CredentialMode.API_KEY): (
        _CredentialVar("api_key", _api_key_var("anthropic"), secret=True),
    ),
    ("openai", CredentialMode.API_KEY): (
        _CredentialVar("api_key", _api_key_var("openai"), secret=True),
    ),
    # Bedrock under ``API_KEY`` passes a bearer token and a region. litellm's
    # ``_sign_request`` reads the bearer off the ``api_key`` parameter, sets the
    # ``Authorization`` header and skips SigV4 entirely, so this is the same
    # shape the other key-bearing vendors use.
    ("bedrock", CredentialMode.API_KEY): (
        _CredentialVar("api_key", _api_key_var("bedrock"), secret=True),
        _CredentialVar("aws_region_name", BEDROCK_REGION_VAR, secret=False),
    ),
    # Bedrock under ``IAM`` passes the region and **no credential**. litellm
    # then falls to its terminal branch and boto3's own chain runs, which covers
    # an attached role, a workload identity binding, ``AWS_PROFILE`` and SSO.
    #
    # litellm's web-identity branch is deliberately not reached: it applies a
    # hardcoded session policy — an IAM permission ceiling with a fixed action
    # list — so a third party's list would decide what this service may call,
    # and a version bump could change it. The terminal branch has no ceiling.
    ("bedrock", CredentialMode.IAM): (
        _CredentialVar("aws_region_name", BEDROCK_REGION_VAR, secret=False),
    ),
    # litellm reads ``GOOGLE_API_KEY`` and then ``GEMINI_API_KEY`` out of the
    # process environment whenever ``api_key`` is absent. Two ambient names
    # for one provider, and the registry declares neither: the key is read
    # from this service's own variable, as it is for every key-bearing vendor.
    ("gemini", CredentialMode.API_KEY): (
        _CredentialVar("api_key", _api_key_var("gemini"), secret=True),
    ),
}

#: The kwargs a ``(vendor, mode)`` pair passes with a **fixed** value, rather
#: than reading one from the environment. Keyed exactly like
#: :data:`_CREDENTIAL_VARS`, and ``tests/test_vendors.py`` checks the two key
#: sets against each other, so a pair cannot answer in one table and be silent
#: in the other.
#:
#: One entry carries a value, and it exists because **"pass no credential
#: material" is not the same as "pass nothing"**. litellm reads
#: ``AWS_BEARER_TOKEN_BEDROCK`` out of the process environment whenever
#: ``api_key`` is ``None`` and authenticates with it, skipping SigV4 — so an
#: *absent* kwarg means "look in the environment", which is the ASI03
#: inherited-credential path this registry exists to close. An empty string
#: states the platform-identity choice positively: litellm tests
#: ``api_key is not None`` first and the value's truthiness second, so ``""``
#: passes the first test, fails the second, and the request is signed from the
#: identity the vendor's own SDK resolved.
#:
#: Stated as a property rather than as one vendor's name: a mode that passes no
#: credential material has to say so to any provider whose SDK would otherwise
#: read one from the environment on its own.
_MODE_KWARGS: dict[tuple[VendorName, CredentialMode], dict[str, str]] = {
    ("vertex", CredentialMode.IAM): {},
    ("anthropic", CredentialMode.API_KEY): {},
    ("openai", CredentialMode.API_KEY): {},
    ("bedrock", CredentialMode.API_KEY): {},
    ("bedrock", CredentialMode.IAM): {"api_key": ""},
    ("gemini", CredentialMode.API_KEY): {},
}

#: What an operator must arrange outside this service, per mode. The diagnostic
#: page reports this beside the declared mode; it never resolves a credential to
#: find out, because that is a network call on page render and it reaches the
#: instance metadata service.
CREDENTIAL_MODE_NOTES: dict[CredentialMode, str] = {
    CredentialMode.API_KEY: (
        "This vendor authenticates with an API key, read from the variable"
        " below and from nowhere else."
    ),
    CredentialMode.IAM: (
        "This vendor passes no credential material. The platform supplies the"
        " identity, and the vendor's SDK resolves it from the environment this"
        " process runs in — a workload identity binding, an attached service"
        " account, or a credentials file the platform's own chain finds. The"
        " variables below address the deployment; none of them is a credential."
    ),
}


@dataclass(frozen=True)
class _FormRule:
    """One model family's pinned-form rule.

    ``family`` is a compiled pattern, matched at the *start* of the model
    identifier; :data:`_CATCH_ALL` carries the empty pattern, which every
    identifier matches. It is a pattern rather than a prefix because a family
    has to be able to run broad while its pinned shape stays strict: a Bedrock
    Claude spelled without ``anthropic.`` must reach this rule and fail the
    shape with a hint, rather than pass unpinned through the catch-all. A
    prefix cannot express that.

    ``pinned`` is the canonical identifier's shape where the vendor documents
    one — Claude's dateless ``claude-{name}-{major}[-{minor}]``. Where a
    family's most specific stable identifier is the bare name (Gemini 2.5 and
    later ship no numbered builds), ``pinned`` is ``None`` and only the
    denylist applies.

    **A rule constrains an identifier's shape, never its version.** This service
    runs any generation a vendor serves. The one place a version is read is
    :func:`~analysis_service.binding.check_temperature`, and what it decides there
    is whether a *sampling param* may be sent, not whether the model may run.
    """

    family: re.Pattern[str]
    pinned: re.Pattern[str] | None
    hint: str


# Floating forms, rejected for every vendor and family. This half stays an
# **open-world denylist** by decision: for families whose vendor publishes no
# canonical form there is nothing to match against, and an allowlist of known
# builds breaks outright the moment a vendor retires one — that risk runs
# against three catalogs. The reproducibility guarantee does not rest here; it
# rests on the *served* build read back from every response.
#
# A floating marker is a **whole word** in the identifier, never a fragment of
# one. A word is a run of letters and digits; every other character delimits.
# There is no delimiter list, and that absence is the decision: the first list
# written for this rule held ``-``, ``_``, ``/`` and ``.``, and it missed ``@``,
# which is what Vertex Model Garden spells its alias with — so
# ``codestral@latest`` reached a run. Testing a fragment failed the other way:
# ``amazon.titan-text-express-v1`` contains ``exp`` inside ``express``, so a
# generally available model was refused with no config knob to fix it.
_WORDS = re.compile(r"[a-z0-9]+", re.IGNORECASE)

#: What an operator is told, by kind of floating form. The two kinds keep two
#: messages because they name different next actions: an alias has a pinned
#: counterpart to look up, and a pre-GA build may have none, so the operator
#: waits for a release rather than searching for a name. Both name the word that
#: matched, so the reason for the refusal is on the screen.
_ALIAS = "is a {word!r} alias"
_PRE_GA = "is a pre-GA {word!r} build"

#: Every word that means the build under this identifier may move, against
#: which kind of floating form it names. One table, one reader
#: (:meth:`Vendor.validate_model`), so a second reader cannot disagree.
#:
#: ``exp`` and ``experimental`` are both listed because a whole-word test does
#: not let one match the other. The alternative — matching a word that *starts
#: with* a marker, with exceptions — re-admits the fragment this table exists to
#: exclude, and its exception list would grow with English rather than with
#: vendors.
#:
#: ``beta``, ``nightly`` and ``dev`` are deliberately absent. Each reads as
#: pre-GA in English and each is a shipping GA name in LiteLLM's pinned cost
#: map: ``xai/grok-3-beta``, ``command-nightly``, ``black_forest_labs/flux-dev``.
#: The property that admits a word is not that it sounds provisional, but that a
#: vendor uses it to mean the build may move.
_FLOATING_WORDS: dict[str, str] = {
    "latest": _ALIAS,
    "preview": _PRE_GA,
    "exp": _PRE_GA,
    "experimental": _PRE_GA,
}

#: An identifier that names a **cloud resource** rather than a model build.
#: Refused for every vendor, on two properties rather than on whose syntax it
#: is. A resource identifier hides which model answers behind it, so a blessed
#: fingerprint would go on certifying a target somebody can repoint at other
#: weights. And it carries the account that owns the resource, which would then
#: reach a fingerprint and a report.
#:
#: ``litellm.get_llm_provider`` accepts an ARN and resolves a provider from it,
#: so this refusal is this service's own and nothing upstream makes it.
#:
#: **Searched, never anchored, and case-blind.** An ARN does not have to start
#: the identifier: litellm routes one behind its own segment, and
#: ``get_llm_provider`` resolves ``bedrock/invoke/arn:…``,
#: ``bedrock/converse/arn:…`` and ``bedrock/anthropic/arn:…`` alike. A rule that
#: read position zero refused the shape an operator would never write and
#: admitted the three a router document shows them.
_RESOURCE_ID = re.compile(r"arn:", re.IGNORECASE)

# Claude is the family that *does* publish a canonical form, so it gets a closed
# shape rather than a denylist. From the 4.6 generation on, the identifier is
# dateless and carries the whole version — and it is a pinned snapshot, not an
# alias: Anthropic ships a new ID rather than moving weights under an existing
# one, and Google Cloud spells it identically. Before 4.6 the pinned build
# carried a date (``-YYYYMMDD`` direct, ``@YYYYMMDD`` on Vertex) and the bare
# name *was* a floating alias, which is why one pattern cannot serve both.
#
# Matching a shape rather than enumerating builds is what keeps this from
# repeating the retired-allowlist failure: a Claude model released tomorrow
# already satisfies it.
#
# The pattern still rejects a dated pre-4.6 identifier such as
# ``claude-3-5-sonnet-20241022``, and that rejection is about its *shape* rather
# than its age: in that era the bare name was itself a floating alias, so the
# dated form cannot be told apart from the aliases this rule exists to reject.
# The minor group is **bounded**, and the major is not. A minor version is one
# or two digits; a date is eight, and an unbounded group read one as the other.
# ``claude-opus-4-20250514`` matched as generation 4.20250514, so
# ``validate_model`` accepted a dated form this comment says it rejects, and
# ``check_temperature`` then read a generation far above its floor and refused
# ``temperature`` on a Claude 4.0. One unbounded group caused both halves.
#
# ``(?!\d)`` is what makes the bound a bound: without it ``\d{1,2}`` would match
# the first two digits of a date and leave the rest to the pattern's tail. The
# major stays ``\d+``, because no generation count is too large to name.
#
# **The two segment orders Claude has ever used, written down once each.** Every
# pattern below is composed from these two atoms, so a spelling is described in
# one place and a fix to it reaches every reader. That is not a style
# preference: the unbounded minor group above was one definition read by
# ``validate_model`` and by ``claude_generation``, and bounding it repaired both
# halves in one edit.
#
# The modern order is name-then-generation (``claude-sonnet-4-6``). The legacy
# order is generation-then-name (``claude-3-5-sonnet``), which Anthropic used
# before 4.6 and which Bedrock still serves under its own published identifiers.
# The two orders name their groups apart, so one alternation can carry both and
# the reader takes whichever matched.
_CLAUDE_MODERN = r"(?P<name>[a-z]+)-(?P<major>\d+)(?:-(?P<minor>\d{1,2})(?!\d))?"
_CLAUDE_LEGACY = r"(?P<lmajor>\d+)(?:-(?P<lminor>\d{1,2})(?!\d))?-(?P<lname>[a-z]+)"

# Direct and Vertex: the modern order alone, because in the legacy era the bare
# name on those vendors *was* a floating alias.
_CLAUDE_ID = re.compile(rf"claude-{_CLAUDE_MODERN}")

# A scope segment, as Bedrock spells one: ``us.``, ``eu.``, ``global.``. Matched
# as a **shape** and never enumerated — the pinned cost map already carries
# seven, three arrived after Claude 3.x, one carries a hyphen, and ``global``
# names no region at all. An allowlist here would repeat what the retired-build
# allowlist cost. A shape admits ``xx.anthropic.claude-opus-5``, which then
# fails at the AWS API: the same class as a misspelled model name, and a shape
# check never proved a model exists.
_SCOPE_SEGMENT = r"(?:[a-z][a-z0-9-]*\.)?"

# **One family pattern, whatever spells it.** A family rule follows the family,
# so which rule reads an identifier must not depend on which vendor's spelling
# the identifier happens to carry. This matches a Claude bare, behind a scope,
# and behind the ``anthropic.`` family segment — every spelling any vendor gives
# the family — and each vendor's own ``pinned`` decides whether that spelling is
# the one it serves.
#
# **The split is load-bearing in both directions.** A Bedrock row spelled
# ``claude-opus-5`` and an ``anthropic`` row spelled ``anthropic.claude-opus-5``
# are the two halves of one mistake: a tier row copied between vendors. Both
# reach a rule and fail its shape with a hint naming the right spelling. With a
# family per spelling, each vendor caught one half and let the other pass
# unpinned to the catch-all, where the config loads and the job dies on node one.
_CLAUDE_FAMILY = re.compile(_SCOPE_SEGMENT + r"(?:anthropic\.)?claude-")

_CLAUDE_RULE = _FormRule(
    family=_CLAUDE_FAMILY,
    pinned=_CLAUDE_ID,
    hint=(
        "a Claude model ID is 'claude-<name>-<major>[-<minor>]',"
        " e.g. 'claude-opus-5' or 'claude-sonnet-4-6'"
    ),
)

# **The dated forms pass here, and that is a property rather than an exception.**
# A dated form is refused where the vendor also serves the bare name as a
# floating alias, because the two cannot be told apart. Bedrock serves no such
# alias: every dateless key in the pinned map names a real dateless model, and
# no ``anthropic.claude-3-5-sonnet`` sits beside the dated one. Refusing the date
# would refuse a model under its published name.
#
# The build tail is part of the canonical AWS identifier and marks no alias. The
# tails the pinned map carries are ``-v1``, ``-v1:0``, ``-v2:0`` and ``-v2:1``.
_BEDROCK_CLAUDE_ID = re.compile(
    _SCOPE_SEGMENT + rf"anthropic\.claude-(?:{_CLAUDE_MODERN}|{_CLAUDE_LEGACY})"
    r"(?:-\d{8})?(?:-v\d+(?::\d+)?)?"
)

# Three forms this shape refuses by decision, each for a stated property. The
# fourth Bedrock refusal — an ARN — is not one of them: it names a resource
# rather than a model and is refused for every vendor by :data:`_RESOURCE_ID`,
# so a rule that lives in the Claude shape would leave a Nova ARN accepted.
#
# * **The ``@date`` spelling.** It is Vertex's, it appears on one key against
#   148, and a second syntax for one generation costs every later reader a
#   branch.
# * **The 2023 names** ``claude-v1``, ``claude-v2:1`` and ``claude-instant-v1``.
#   The shape requires a family name and a generation, because those two
#   segments are what a later reader keys on, and these three predate that
#   naming.
# * **A Claude that omits ``anthropic.``**, per the shared family above. It is
#   the shape a config copying an anthropic-direct tier row into the bedrock row
#   produces, and it fails here with a hint rather than passing unpinned.
_BEDROCK_CLAUDE_RULE = _FormRule(
    family=_CLAUDE_FAMILY,
    pinned=_BEDROCK_CLAUDE_ID,
    hint=(
        "a Bedrock Claude model ID is"
        " '[<scope>.]anthropic.claude-<name>-<major>[-<minor>]'"
        " with an optional date and build tail,"
        " e.g. 'anthropic.claude-opus-5' or"
        " 'us.anthropic.claude-sonnet-4-5-20250929-v1:0'"
    ),
)

# The families this service requires no canonical shape from: Gemini, on either
# Google vendor, and OpenAI's own models. Only the shared denylist applies.
#
# Gemini 2.5 and later publish no numbered builds, so the bare name is the most
# specific identifier that exists and there is nothing to require. That holds
# whichever route serves the family, so the ``gemini`` row needs no rule the
# ``vertex`` row does not already carry.
#
# **OpenAI's gpt-* family is different, and the rule stays open by decision
# rather than by absence.** That family does publish dated snapshots, and the
# bare name is an alias to one of them. Measured live against the API:
#
#     requested 'gpt-4o'            -> response model 'gpt-4o-2024-08-06'
#     requested 'gpt-4o-2024-08-06' -> response model 'gpt-4o-2024-08-06'
#
# So the alias resolves, and the response names the build that actually served
# rather than echoing the request. That is what makes refusing the alias
# unnecessary here: a fingerprint binds the *served* build beside the requested
# route, ``openai`` is ``provider_reported``, and OpenAI moving the alias to a
# different snapshot therefore moves every fingerprint and fails certification
# closed. The reproducibility guarantee rests on the readback, exactly as
# :class:`_FormRule` says it does, and this is the family that demonstrates it.
#
# The o-series is a separate case again: it ships no dated form at all.
_CATCH_ALL = _FormRule(family=re.compile(""), pinned=None, hint="")

# Which family rules each vendor applies, in order, with the catch-all last.
#
# **A vendor's entry lists every family that vendor can serve, and the rule
# itself is a property of the family.** The entry is keyed by vendor only
# because one family can be spelled differently on different vendors, which is
# what a future row serving ``anthropic.claude-…`` will need — not because a
# vendor decides what a Claude identifier looks like.
#
# ``openai`` listed the catch-all alone, and that was the defect: the prefix
# reaches any OpenAI-compatible endpoint, and a gateway serving Claude passes
# the vendor's own identifier straight through. So ``claude-3-opus`` — a
# floating alias — and ``claude-opus-4-20250514`` — a dated form — were refused
# on two vendors and accepted on the third. An operator moving a tier between
# vendors met a different set of legal identifiers, which is the disagreement
# the pinned-form rule exists to remove.
#
# This is the mirror of the reason ``check_temperature`` and
# ``openai_reasoning_model`` refuse to key on the vendor at all: nothing stops a
# family arriving through a gateway under a vendor that did not train it, and
# the number of routes to one family only ever grows.
_FORM_RULES: dict[VendorName, tuple[_FormRule, ...]] = {
    "vertex": (_CLAUDE_RULE, _CATCH_ALL),
    "anthropic": (_CLAUDE_RULE, _CATCH_ALL),
    "openai": (_CLAUDE_RULE, _CATCH_ALL),
    # Bedrock reads the same family under its own spelling. Everything else it
    # serves — Nova, Llama, Mistral and 28 more families — reaches the catch-all,
    # where only the shared denylist applies, scope segment and all.
    "bedrock": (_BEDROCK_CLAUDE_RULE, _CATCH_ALL),
    # The Developer API serves Gemini alone today, and it still lists the
    # Claude rule: ``get_llm_provider`` resolves ``gemini/claude-opus-5`` to
    # this provider without complaint, and a family rule follows the family.
    # A Claude identifier arriving here gets the verdict every bare-spelling
    # vendor gives it, and a tier row moved between ``gemini`` and ``vertex``
    # meets one set of legal identifiers.
    "gemini": (_CLAUDE_RULE, _CATCH_ALL),
}


# The parse, composed from the same two atoms as the rules above and reading a
# Claude wherever one starts a segment. ``(?:^|\.)`` is what earns that: it
# requires the match to start the identifier or to follow a dot, so a ``claude-``
# inside a word — ``my-claude-clone-3`` — is not a Claude.
_CLAUDE_GENERATION = re.compile(
    rf"(?:^|\.)claude-(?:{_CLAUDE_MODERN}|{_CLAUDE_LEGACY})"
)


def claude_generation(model: str) -> tuple[int, int] | None:
    """The ``(major, minor)`` a Claude identifier names, or ``None`` if it isn't one.

    A major-version release omits the minor segment (``claude-opus-5``), so an
    absent group reads as ``.0`` rather than as a parse failure. Both segment
    orders are read, because ``claude-3-5-sonnet`` and ``claude-sonnet-4-5``
    name a generation the same way and spell it in reverse.

    **Vendor-blind by decision, and not because the caller lacks the vendor.**
    :func:`~analysis_service.binding.build_tier_adapters` holds one and hands it
    to its neighbours. A parse that asked :meth:`Vendor._rule_for` which pattern
    to read would return the catch-all under ``openai``, whose ``pinned`` is
    ``None``, so a Claude reached through an OpenAI-compatible gateway would
    parse to ``None`` and the temperature floor would go silent. A model
    family's sampling surface is a property of the weights, and the number of
    routes to one family only ever grows.

    A generation decides which params a model accepts, never whether this
    service will run it.
    """
    match = _CLAUDE_GENERATION.search(model)
    if match is None:
        return None
    major = match["major"] or match["lmajor"]
    minor = match["minor"] or match["lminor"]
    return int(major), int(minor or 0)


# OpenAI's reasoning families serve ``temperature`` at exactly its default of
# 1 and reject any other value. Two shapes name one: the o-series (``o3``,
# ``o4-mini``) and GPT from major 5 onward (``gpt-5.6-terra``). ``gpt-4o`` is
# neither, and parses to major 4 rather than being special-cased.
_O_SERIES_ID = re.compile(r"o\d+[a-z0-9.\-]*")
_GPT_ID = re.compile(r"gpt-(?P<major>\d+)(?:\.\d+)?[a-z0-9.\-]*")
_REASONING_FROM_GPT_MAJOR = 5


def openai_reasoning_model(model: str) -> bool:
    """Whether an OpenAI identifier names a family that pins ``temperature``.

    A **family rule, not a support table**, for the reason the Claude floor in
    :mod:`analysis_service.binding` is a generation floor: mirroring what LiteLLM
    computes forks a subsystem that drifts, while a rule keyed on the
    identifier stops being load-bearing — rather than starting to contradict —
    once LiteLLM's map catches up.

    Open at the top on purpose. An unrecognised ``gpt-6`` reads as reasoning
    and a config pinning ``temperature = 0.0`` for it fails the build. That is
    the safe direction to be wrong in: a false positive costs one clear error
    at startup, while a false negative costs node one of a paid-for job.
    """
    if _O_SERIES_ID.fullmatch(model):
        return True
    match = _GPT_ID.fullmatch(model)
    return match is not None and int(match["major"]) >= _REASONING_FROM_GPT_MAJOR


@dataclass(frozen=True)
class Vendor:
    """One provider's registry entry."""

    name: VendorName
    prefix: str
    #: What this vendor's served build identifier is worth, under the pinned
    #: translator. A field rather than a module table, so ``VENDORS`` is the
    #: table and a fourth vendor cannot construct without an answer.
    #:
    #: This is a property of the vendor **and** of the translator that reads it,
    #: which is why ``tests/test_identity.py`` drives each vendor's installed
    #: transformation with a canned response rather than restating the value. A
    #: litellm bump that started reading ``modelVersion`` would make the
    #: ``vertex`` entry wrong, and every fingerprint would move on that bump
    #: anyway — ``litellm`` sits in ``BUILD_DISTRIBUTIONS`` — so the hashes
    #: would move for an unrelated reason and the stale entry would stay
    #: invisible.
    served_trust: ServedTrust

    @property
    def litellm_provider(self) -> str:
        """The provider token LiteLLM dispatches on — the prefix without its slash.

        Derived rather than stored so the prefix stays the single source: the
        router string and the gate's ``custom_llm_provider`` can never disagree.
        """
        return self.prefix.rstrip("/")

    @property
    def api_key_var(self) -> str:
        """The env var holding this vendor's API key, where it uses one."""
        return _api_key_var(self.name)

    @property
    def credential_modes(self) -> tuple[CredentialMode, ...]:
        """Every mode this vendor allows, from :data:`CREDENTIAL_MODES`."""
        return CREDENTIAL_MODES[self.name]

    @property
    def sole_credential_mode(self) -> CredentialMode:
        """This vendor's mode where it allows exactly one, or raise.

        A vendor with a choice has to be asked which one a deployment declared,
        and the config is what holds that answer. Raising here rather than
        picking the first entry is what stops a caller that never learned about
        the choice from silently making it.
        """
        modes = self.credential_modes
        if len(modes) != 1:
            raise ValueError(
                f"vendor {self.name!r} allows {len(modes)} credential modes,"
                " so the deployment declares which one; ask the tier config"
            )
        return modes[0]

    def route(self, model: str) -> str:
        """The router string LiteLLM dispatches on, e.g. ``vertex_ai/gemini-2.5-pro``.

        The same join is the vendor half of a fingerprint: the served
        identifier carries no vendor, and Vertex-hosted Claude and
        Anthropic-direct return through an identical transformation, so a
        served-only hash would let a manifest blessed on one silently certify
        the other.
        """
        return f"{self.prefix}{model}"

    def validate_model(self, model: str, source: str) -> str:
        """Reject a floating identifier; return the pinned one.

        A **shape** check only: no generation is too old to name here, so a
        build a vendor still serves is one this service will still run. What is
        refused is an identifier that names something other than a build —
        a floating form, or a cloud resource.

        A floating word is matched as a whole word rather than as a fragment,
        and :data:`_FLOATING_WORDS` is the one table this reads. A false
        refusal is *not* the safe direction here, which is where this differs
        from :func:`openai_reasoning_model` — that rule records the opposite for
        itself. A false refusal is a hard stop needing a code change and a
        release before the operator can run a model their vendor ships. A false
        acceptance runs a pre-GA build, and the reproducibility guarantee rests
        on the served build read back from every response rather than on this
        check.

        ``source`` names where the string came from (a config key or an env var)
        so the error points ops at the knob to turn.
        """
        if not model or model != model.strip():
            raise ValueError(f"{source}: {model!r} is not a model identifier")
        if _RESOURCE_ID.search(model):
            raise ValueError(
                f"{source}: {model!r} names a cloud resource rather than a model"
                " build; a resource can be repointed at other weights and its"
                " identifier carries an account, so name the model itself"
            )
        word = next(
            (w for w in _WORDS.findall(model.lower()) if w in _FLOATING_WORDS), None
        )
        if word is not None:
            reason = _FLOATING_WORDS[word].format(word=word)
            raise ValueError(
                f"{source}: {model!r} {reason}; use the pinned model identifier"
            )
        rule = self._rule_for(model)
        if rule.pinned is not None and rule.pinned.fullmatch(model) is None:
            raise ValueError(f"{source}: {model!r} is not pinned — {rule.hint}")
        return model

    def _rule_for(self, model: str) -> _FormRule:
        """The first family rule matching this model; the catch-all always does."""
        rules = _FORM_RULES[self.name]
        return next(rule for rule in rules if rule.family.match(model) is not None)

    def credential_kwargs(
        self, env: Mapping[str, str], mode: CredentialMode
    ) -> dict[str, str]:
        """The auth kwargs for this vendor's ``LiteLlm``, or fail closed.

        Read **explicitly** from vendor-scoped variables rather than relying on
        LiteLLM's ambient ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` pickup, so
        no credential this deployment did not declare can authenticate a run —
        an undeclared key in the process environment is exactly the ASI03
        inherited-credential path.

        Under a mode that passes no credential material this returns the
        vendor's addressing kwargs, plus whatever :data:`_MODE_KWARGS` says that
        pair must state positively — because for one provider "no credential"
        has to be said out loud or the library reads one from the environment.
        That is what lets the vendor's SDK resolve the platform's identity and
        nothing else. See :class:`CredentialMode` for why the mechanism is
        still declared.

        Long-lived API keys are accepted with controls, not avoided: none of
        these vendors issues a short-lived token, so the residual risk is real
        and is mitigated by keeping keys env-only, out of logs, out of the
        report, and out of the fingerprint, plus rotation.
        """
        kwargs = dict(_MODE_KWARGS.get((self.name, mode), {}))
        kwargs.update(
            {
                entry.kwarg: self._require(env, entry.var, mode)
                for entry in self._credential_vars(mode)
            }
        )
        return kwargs

    def _credential_vars(self, mode: CredentialMode) -> tuple[_CredentialVar, ...]:
        """This ``(vendor, mode)`` pair's entries, or raise on an unallowed mode.

        One table, three readers: :meth:`credential_kwargs` builds the adapter's
        auth from it, :meth:`required_env_vars` reports it and
        :meth:`secret_env_vars` says which values must never be echoed.
        Deriving all three from the same place is the point — a vendor -> env-var
        table copied into a caller drifts from the check that actually runs, and
        one of those callers is a *diagnostic* page whose whole value is being
        right about which variables are missing.
        """
        try:
            return _CREDENTIAL_VARS[self.name, mode]
        except KeyError as exc:
            allowed = ", ".join(sorted(m.value for m in self.credential_modes))
            raise ValueError(
                f"vendor {self.name!r} has no {mode.value!r} credential mode"
                f" (it allows: {allowed})"
            ) from exc

    def required_env_vars(self, mode: CredentialMode) -> tuple[str, ...]:
        """Every environment variable this vendor needs, in check order.

        :meth:`_require` raises on the *first* missing variable, so an operator
        with none of them set would otherwise discover them one per restart.
        Callers reporting a credential failure list this whole set and mark the
        unset ones — presence only, never values (OWASP A09).
        """
        return tuple(entry.var for entry in self._credential_vars(mode))

    def secret_env_vars(self, mode: CredentialMode) -> tuple[str, ...]:
        """Only the variables holding credential material.

        A narrower question than :meth:`required_env_vars` answers, and the
        difference matters: a caller redacting provider error text must remove
        a key and must **not** remove a region. One list answering both
        questions removed the region, which is the one fact that diagnoses a
        wrong-region request.
        """
        return tuple(entry.var for entry in self._credential_vars(mode) if entry.secret)

    def _require(self, env: Mapping[str, str], var: str, mode: CredentialMode) -> str:
        value = env.get(var, "")
        if not value.strip():
            raise ProviderAuthError(
                f"vendor {self.name!r} needs {var};"
                f" it is unset or empty (credential mode: {mode.value})"
            )
        return value.strip()


VENDORS: dict[VendorName, Vendor] = {
    "vertex": Vendor(
        name="vertex",
        prefix="vertex_ai/",
        # litellm fills ``model_response.model`` from the request in its Gemini
        # transformation. The response body carries ``modelVersion`` and litellm
        # never reads it.
        served_trust="requested_echo",
    ),
    "anthropic": Vendor(
        name="anthropic",
        prefix="anthropic/",
        # litellm reads ``completion_response["model"]`` in its Anthropic chat
        # transformation.
        served_trust="provider_reported",
    ),
    "openai": Vendor(
        name="openai",
        prefix="openai/",
        # litellm reads ``response_object["model"]`` when it converts an
        # OpenAI-shaped response dict.
        served_trust="provider_reported",
    ),
    "bedrock": Vendor(
        name="bedrock",
        # ``bedrock/`` and not ``bedrock_converse/``: ``get_llm_provider``
        # resolves the first and refuses the second, even though the pinned cost
        # map labels these models ``bedrock_converse`` internally.
        prefix="bedrock/",
        # A Converse response carries no model identifier at all, so litellm
        # fills ``model_response.model`` from the request.
        served_trust="requested_echo",
    ),
    # The Gemini Developer API: the same weights ``vertex`` serves, behind a
    # key instead of a platform identity. Two rows and not one, because the
    # router prefix is the vendor half of an Execution Identity and litellm
    # dispatches the two prefixes to two providers. A deployment may select
    # both; the prefixes differ, so :func:`vendor_for_route` tells the routes
    # apart and a fingerprint blessed on one never certifies the other.
    "gemini": Vendor(
        name="gemini",
        prefix="gemini/",
        # litellm's Developer API config inherits the Vertex Gemini
        # transformation, which fills ``model_response.model`` from the
        # request and never reads the body's ``modelVersion``.
        served_trust="requested_echo",
    ),
}


@dataclass(frozen=True)
class VendorSdk:
    """The client library one vendor's provider needs, and what supplies it.

    ``module`` is the name probed with :func:`importlib.util.find_spec`, and
    ``extra`` is the ``pip install analysis-service[<extra>]`` that installs it.
    """

    module: str
    extra: str


#: Which client library each vendor's provider needs in the image, keyed by
#: vendor. ``None`` is a real answer and not an absent one: every vendor states
#: whether it needs a library, so the completeness guard in
#: ``tests/test_vendor_neutrality.py`` can see a row that never answered.
#:
#: Only a provider that signs or resolves its own requests needs a library here.
#: The other four reach an HTTPS endpoint with a bearer token or with a
#: credential ``google-auth`` already resolves, and litellm carries both.
#:
#: An optional extra rather than a wheel dependency, per ADR 0023: a deployment
#: that never selects this vendor should not carry its SDK. Both of Bedrock's
#: credential modes need ``boto3`` and not ``botocore`` alone —
#: ``converse_handler`` calls ``get_credentials`` before anything reads a bearer
#: token, and with no AWS kwarg that chain ends in a bare ``import boto3``.
VENDOR_SDKS: dict[VendorName, VendorSdk | None] = {
    "vertex": None,
    "anthropic": None,
    "openai": None,
    "bedrock": VendorSdk(module="boto3", extra="bedrock"),
    "gemini": None,
}


class VendorSdkError(ConfigError):
    """A selected vendor's client library is not installed.

    Distinct from :class:`ProviderAuthError`, which is about credential material
    a deployment holds and did not declare. This one is about the image: the
    declaration is fine and the library that would use it is absent, so the
    message names the extra to install rather than a variable to set.
    """


def sdk_for(name: VendorName) -> VendorSdk | None:
    """The client library this vendor's provider needs, or ``None``.

    The one reader of :data:`VENDOR_SDKS`. Two callers ask two different
    questions — the diagnostic page asks *which* library, and :func:`missing_sdk`
    asks whether it is there — and both go through here, because ``None`` from
    :func:`missing_sdk` answers "needs nothing" and "already installed" alike.
    A page reading the table itself would keep printing a row from it the day
    that answer became conditional.
    """
    return VENDOR_SDKS[name]


def missing_sdk(name: VendorName) -> VendorSdk | None:
    """The client library this vendor needs and this image does not carry.

    Called by the build-time gate in :mod:`analysis_service.binding` and by the
    diagnostic page. A page that marked every variable "set" while the run still
    failed at bind time would break the no-drift promise it is built on.

    :func:`importlib.util.find_spec` and **never** an import.
    ``tests/test_identity.py`` asserts that every import ``src/`` makes is
    declared in ``project.dependencies``, so ``import boto3`` would turn an
    optional extra into a hard dependency of the wheel.
    """
    sdk = sdk_for(name)
    if sdk is None or importlib.util.find_spec(sdk.module) is not None:
        return None
    return sdk


def require_sdk(name: VendorName) -> None:
    """Fail closed when a selected vendor's client library is absent.

    Once per bound tier, beside the credential check and for the same reason: a
    tier nothing runs on costs no SDK, exactly as it costs no credential.
    """
    sdk = missing_sdk(name)
    if sdk is not None:
        raise VendorSdkError(
            f"vendor {name!r} needs {sdk.module}; install analysis-service[{sdk.extra}]"
        )


def vendor_for_route(route: str) -> Vendor:
    """The vendor a router string belongs to, by its prefix.

    The **one reader of the route-to-vendor rule**, built by inverting
    :data:`VENDORS` on :attr:`Vendor.prefix` rather than from a hand-written
    prefix map. A second map would answer this question differently the first
    time a prefix changed, and its own test would agree with it.

    Raises on a bare name and on a prefix no vendor claims. Both are a caller
    holding a string that is not a route, and inventing a vendor for one is how
    a fingerprint comes to name a provider that never ran.
    """
    prefix, separator, _ = route.partition("/")
    if not separator:
        raise ValueError(
            f"{route!r} is not a router string: it carries no vendor prefix"
        )
    for vendor in VENDORS.values():
        if vendor.prefix == f"{prefix}{separator}":
            return vendor
    known = ", ".join(sorted(vendor.prefix for vendor in VENDORS.values()))
    raise ValueError(
        f"no vendor serves the prefix {prefix + separator!r} (known: {known})"
    )


def join_served(requested_route: str, served_model: str) -> str:
    """Re-attach a requested route's vendor prefix to the build that answered.

    Providers return a bare build identifier — ``gemini-2.5-pro-002``, not
    ``vertex_ai/gemini-2.5-pro-002`` — so the vendor has to come from what was
    asked for. That join is what keeps a fingerprint honest: Vertex-hosted
    Claude and Anthropic-direct return through an identical transformation, so
    a served-only hash would let a manifest blessed on one silently certify the
    other.

    The prefix comes from :func:`vendor_for_route` rather than from the string,
    so this and every other reader of the rule agree by construction. A route
    that names no vendor raises here, which is what the producer's own
    invariant already guarantees: ``node_models`` is built from the tier config
    through :attr:`Vendor.prefix`.
    """
    return f"{vendor_for_route(requested_route).prefix}{served_model}"


def vendor_for(name: str) -> Vendor:
    """The registry entry for a vendor name, or raise."""
    if name not in VENDORS:
        known = ", ".join(VENDOR_NAMES)
        raise ValueError(f"unknown vendor {name!r} (known: {known})")
    return VENDORS[name]
