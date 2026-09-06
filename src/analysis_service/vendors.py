"""The vendor registry: the per-provider facts nothing else will carry.

Every model reaches the graph through ADK's ``LiteLlm``, so what varies per
provider is not how to call it. It is three facts the adapter cannot supply:

* the router prefix LiteLLM dispatches on — ``vertex_ai/``, ``anthropic/`` or
  ``openai/`` — which is also the vendor half of an **Execution Identity**
  fingerprint;
* the credential modes a vendor allows, which :data:`CREDENTIAL_MODES` holds and
  a deployment declares from. Vertex admits no raw-API-key path under any
  adapter (``BerriAI/litellm#21036``), so ``vertex + api_key`` is
  unrepresentable rather than validated against;
* the floating-form rule for model identifiers, which differs by model family
  rather than by vendor. Claude carries a canonical identifier of its own shape,
  and both vendors that serve it spell that shape the same way.

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

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from analysis_service.errors import ConfigError

VendorName = Literal["vertex", "anthropic", "openai"]
VENDOR_NAMES: tuple[VendorName, ...] = ("vertex", "anthropic", "openai")

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
#: Every vendor here allows exactly one mode, so no deployment has a choice to
#: declare yet. The loader's rules are what carry that: a ``[credentials]`` key
#: for a single-mode vendor is an error, and a missing key for a multi-mode
#: vendor is an error. Both read this table.
CREDENTIAL_MODES: dict[VendorName, tuple[CredentialMode, ...]] = {
    "vertex": (CredentialMode.IAM,),
    "anthropic": (CredentialMode.API_KEY,),
    "openai": (CredentialMode.API_KEY,),
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
_CLAUDE_ID = re.compile(r"claude-[a-z]+-(?P<major>\d+)(?:-(?P<minor>\d{1,2})(?!\d))?")

_CLAUDE_RULE = _FormRule(
    family=re.compile(r"claude-"),
    pinned=_CLAUDE_ID,
    hint=(
        "a Claude model ID is 'claude-<name>-<major>[-<minor>]',"
        " e.g. 'claude-opus-5' or 'claude-sonnet-4-6'"
    ),
)

# The families this service requires no canonical shape from: Gemini on Vertex,
# and OpenAI's own models. Only the shared denylist applies.
#
# Gemini 2.5 and later publish no numbered builds, so the bare name is the most
# specific identifier that exists and there is nothing to require.
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
}


def claude_generation(model: str) -> tuple[int, int] | None:
    """The ``(major, minor)`` a Claude identifier names, or ``None`` if it isn't one.

    A major-version release omits the minor segment (``claude-opus-5``), so an
    absent group reads as ``.0`` rather than as a parse failure.

    Public because its one caller cannot key on the *vendor*: the build-time
    sampling rule in :mod:`analysis_service.binding` has to know which Claude
    generation it is binding, whether that Claude arrives direct or via Vertex.
    A generation decides which params a model accepts, never whether this
    service will run it.
    """
    match = _CLAUDE_ID.fullmatch(model)
    if match is None:
        return None
    return int(match["major"]), int(match["minor"] or 0)


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
        build a vendor still serves is one this service will still run.

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
        vendor's addressing kwargs and nothing else, which is what lets the
        vendor's SDK resolve the platform's identity. See
        :class:`CredentialMode` for why the mechanism is still declared.

        Long-lived API keys are accepted with controls, not avoided: none of
        these vendors issues a short-lived token, so the residual risk is real
        and is mitigated by keeping keys env-only, out of logs, out of the
        report, and out of the fingerprint, plus rotation.
        """
        return {
            entry.kwarg: self._require(env, entry.var, mode)
            for entry in self._credential_vars(mode)
        }

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
}


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
