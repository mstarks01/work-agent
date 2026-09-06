"""What a ``(vendor, model)`` can be asked for, as three answers rather than two.

Every gate in :mod:`analysis_service.binding` is a raise. It decides whether one
tier's configuration may bind, and a question it cannot answer is not its
problem, because letting an unmapped model through is the right call for a gate.
This module asks the same questions for a different purpose, which is to report::

    SUPPORTED    the provider accepts it, and the map is what says so
    UNSUPPORTED  the provider rejects it
    UNKNOWN      nothing here knows — the model is not in the pinned map

``UNKNOWN`` is the whole reason the module exists. A capability matrix that
renders it as ``UNSUPPORTED`` invents a fact, and one that renders it as
``SUPPORTED`` invents a worse one. The honest cell is the one that says the
question went unanswered. Vendor neutrality is equivalent application behaviour
given equivalent provider capabilities, and nobody can check that claim without
being able to see which capabilities differ.

The module is credential-free by construction. Every probe here is a call into
the pinned ``litellm``'s local model-cost map; see
:func:`~analysis_service.model_gate._import_litellm_hermetically`. A full matrix
for every vendor is therefore computable in the offline CI lane, with no
key, no ADC and no egress. That is what makes this suite runnable on every pull
request, rather than in a live sweep nobody has provisioned. It is also the
limit of what it proves: this module reports what a provider would accept, and
never what a model returns. Nothing here is evidence that a vendor has served a
request.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Imported before anything that could pull in ``litellm``, per the ordering
# rule in :mod:`analysis_service.model_gate`.
from analysis_service.model_gate import (
    check_supported,
    emulates_structured_output,
    model_info,
    output_ceiling,
    supports_structured_output,
)
from analysis_service.vendors import REASONING_KWARG, Vendor, vendor_for


class Capability(StrEnum):
    """Whether a provider will accept one thing, including "nobody knows"."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


# The params probed, each with the value the probe sends. A value, not just a
# name, because some of these constraints are on the **value** rather than the
# parameter: o-series ``temperature`` must be exactly ``1``, so "does this model
# support temperature" has no vendor-neutral answer and only "would this model
# accept temperature=0.0" does. The values are ones this deployment may ask for
# — the offered sampling surface, at a representative value per param — so a
# cell reads as *this deployment could ask for that*, which is the only reading
# a conformance matrix can act on. Representative rather than shipped, and that
# distinction is the point: `temperature`, `top_p` and `seed` are all unset in
# `config/sampling.toml`, so a matrix keyed on the shipped values would report
# nothing about the params an operator is most likely to reach for.
#
# ``max_output_tokens`` is deliberately absent: every vendor accepts the
# parameter and only the serving model objects to the value, so it is reported
# as a ceiling by :attr:`ProviderProfile.output_ceiling` rather than as a
# yes/no. That is the same split :func:`~analysis_service.model_gate.output_ceiling`
# documents for the gate.
PROBED_PARAMS: dict[str, Any] = {
    "temperature": 0.0,
    "top_p": 0.95,
    "seed": 1,
    REASONING_KWARG: "low",
}


# The pairs this project claims to have profiled, one per tier per vendor.
#
# In ``src/`` rather than under ``tests/`` because it is not a fixture: it is
# the *extent* of the conformance claim — "every supported vendor shares a
# common conformance suite" is only meaningful alongside the list of what was
# actually put through it. Two readers need exactly this list and must not each
# keep their own: the offline suite in ``tests/test_conformance.py``, and the
# CI step that renders the matrix into a job summary.
#
# These are the same pairs the live workflows pin, and that is not a
# coincidence to be maintained by hand — ``tests/test_conformance.py`` asserts
# every model here appears in the workflow that would sweep it, so a live lane
# pinned to a model nobody profiled fails the offline suite.
#
# Ordering within the tuple is base-tier then strong-tier. Ordering *between*
# vendors is alphabetical, deliberately: any other order here is a ranking, and
# the vendors are alphabetical everywhere a reader might infer one.
REFERENCE_MODELS: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude-sonnet-4-6", "claude-opus-5"),
    # Claude in its Bedrock spelling, and only Claude: Nova and Llama get
    # *emulated* structured output there, which
    # :func:`~analysis_service.binding._check_native_structured_output` refuses,
    # so neither could bind a tier of this graph. The plain identifier rather
    # than a region-scoped one, because no region enters an Execution Identity.
    "bedrock": ("anthropic.claude-sonnet-4-6", "anthropic.claude-opus-5"),
    # The weights the ``vertex`` pair names, behind the Developer API. The
    # same identifiers on purpose: the matrix is where the two routes to one
    # family show their one difference, and a different pair would hide it.
    "gemini": ("gemini-2.5-flash", "gemini-2.5-pro"),
    # The dated snapshot rather than the ``gpt-4o`` alias it resolves to.
    # OpenAI fronts its dated builds with a bare name and moves which build that
    # name means; the matrix is a claim about what was profiled, so it names the
    # build. Nothing else moves with it: the pinned cost map's entries for the
    # alias and this snapshot differ in no key, and the two profile identically.
    # ``test_no_reference_model_is_an_alias_for_a_dated_build`` is the rule.
    "openai": ("gpt-4o-2024-08-06", "gpt-5.6"),
    "vertex": ("gemini-2.5-flash", "gemini-2.5-pro"),
}


@dataclass(frozen=True)
class ProviderProfile:
    """One ``(vendor, model)`` pair's capabilities, as this deployment sees them.

    ``params`` carries one :class:`Capability` per entry in
    :data:`PROBED_PARAMS`. ``structured_output`` is separate because it is not
    the same question: ``response_format`` is an accepted parameter everywhere,
    and what varies is whether the provider honours it as a schema natively or
    LiteLLM emulates it with a synthesised tool — a difference the parameter
    probe cannot see and one that fails a job at output validation rather than
    at the request (see
    :func:`~analysis_service.binding._check_native_structured_output`).

    ``known`` records whether the pinned map had an entry at all. It is not
    derivable from the cells: an unmapped model produces all-``UNKNOWN``, but so
    could a mapped one whose every probe was inconclusive, and a reader deciding
    whether to trust this profile needs to tell those apart.
    """

    vendor: str
    model: str
    known: bool
    params: dict[str, Capability]
    structured_output: Capability
    output_ceiling: int | None

    def to_json(self) -> dict[str, Any]:
        """The profile as a rendered row, for a CI summary or an artifact."""
        return {
            "vendor": self.vendor,
            "model": self.model,
            "known_to_model_map": self.known,
            "params": {name: str(value) for name, value in self.params.items()},
            "structured_output": str(self.structured_output),
            "output_ceiling": self.output_ceiling,
        }

    @property
    def unknowns(self) -> tuple[str, ...]:
        """Every capability this profile could not answer, for an explicit report.

        Named rather than counted, for the reason
        :class:`~evals.harness.calibration.Disagreement` is kept whole: a count
        of unanswered questions tells a reader that coverage is incomplete and
        not which coverage, and the second is the actionable half.
        """
        unanswered = [
            name
            for name, capability in self.params.items()
            if capability is Capability.UNKNOWN
        ]
        if self.structured_output is Capability.UNKNOWN:
            unanswered.append("structured_output")
        return tuple(sorted(unanswered))


def _probe_param(vendor: Vendor, model: str, name: str, value: Any) -> Capability:
    """Whether this provider would accept one param at one value.

    Reuses :func:`~analysis_service.model_gate.check_supported` rather than
    re-asking LiteLLM directly, so a cell in this matrix and the build-time gate
    can never disagree about the same pair — the report would otherwise say
    ``SUPPORTED`` for a combination the build refuses, which is the failure this
    module is least able to afford.
    """
    try:
        check_supported(vendor, model, {name: value}, source="conformance probe")
    except Exception:  # noqa: BLE001 -- check_supported wraps litellm's bare raise
        return Capability.UNSUPPORTED
    return Capability.SUPPORTED


def _probe_structured_output(vendor: Vendor, model: str) -> Capability:
    """Whether schema-constrained output reaches this model *natively*.

    ``UNSUPPORTED`` covers both ways a model fails to get it: the provider
    library not honouring a schema at all, and honouring it only through
    LiteLLM's synthesised-tool emulation. They are collapsed deliberately —
    every LLM node in the graph binds an output schema, so for this application
    an emulated constraint is not a lesser form of support but a job that dies
    at output validation, and a matrix that graded it as partial support would
    invite exactly the deployment the build-time gate exists to refuse.
    """
    if not supports_structured_output(vendor, model):
        return Capability.UNSUPPORTED
    if emulates_structured_output(vendor, model):
        return Capability.UNSUPPORTED
    return Capability.SUPPORTED


def profile(vendor: Vendor, model: str) -> ProviderProfile:
    """Probe one ``(vendor, model)`` pair. No credentials, no network, no call.

    A model the pinned map does not carry yields ``UNKNOWN`` for every cell
    rather than the answer LiteLLM's provider-base-config fallback would give.
    That fallback is frequently correct, and reporting it as though it were
    model-specific is what turns an open-world residual into a false assurance:
    the cells would say a model was checked when the check never saw it.
    """
    known = model_info(vendor, model) is not None
    if not known:
        return ProviderProfile(
            vendor=vendor.name,
            model=model,
            known=False,
            params=dict.fromkeys(PROBED_PARAMS, Capability.UNKNOWN),
            structured_output=Capability.UNKNOWN,
            output_ceiling=None,
        )
    return ProviderProfile(
        vendor=vendor.name,
        model=model,
        known=True,
        params={
            name: _probe_param(vendor, model, name, value)
            for name, value in PROBED_PARAMS.items()
        },
        structured_output=_probe_structured_output(vendor, model),
        output_ceiling=output_ceiling(vendor, model),
    )


def reference_matrix() -> tuple[ProviderProfile, ...]:
    """Every pair in :data:`REFERENCE_MODELS`, profiled, in the order listed."""
    return tuple(
        profile(vendor_for(vendor), model)
        for vendor, models in REFERENCE_MODELS.items()
        for model in models
    )


def render_markdown(profiles: Sequence[ProviderProfile]) -> str:
    """The matrix as a Markdown table, for a CI job summary.

    Every cell is one of the three words. A pair the map does not carry renders
    as a full row of ``unknown`` rather than being omitted, because the row is
    the point: a vendor missing from the table reads as a vendor nobody thought
    about, and a vendor present with unanswered cells reads as what it is.
    """
    columns = (*PROBED_PARAMS, "structured_output")
    lines = [
        "| vendor | model | " + " | ".join(columns) + " | output ceiling |",
        "| --- " * (len(columns) + 3) + "|",
    ]
    for entry in profiles:
        cells = [str(entry.params[name]) for name in PROBED_PARAMS]
        cells.append(str(entry.structured_output))
        ceiling = (
            "unknown" if entry.output_ceiling is None else str(entry.output_ceiling)
        )
        lines.append(
            f"| {entry.vendor} | `{entry.model}` | "
            + " | ".join(cells)
            + f" | {ceiling} |"
        )
    return "\n".join(lines)


def main() -> None:
    """Render the reference matrix. ``python -m analysis_service.conformance``.

    Markdown on stdout, so a CI step is a redirect into ``$GITHUB_STEP_SUMMARY``
    and a developer running it locally gets something readable. Exits zero
    regardless of what the matrix says: an ``unsupported`` cell is a capability
    difference rather than a failure, which is the distinction the whole module
    rests on. What *does* fail is the offline suite, and only for the pairs this
    service would refuse to bind.
    """
    profiles = reference_matrix()
    print(render_markdown(profiles))
    unanswered = {
        f"{entry.vendor}/{entry.model}": entry.unknowns
        for entry in profiles
        if entry.unknowns
    }
    if unanswered:
        print("\nUnanswered — not in the pinned model map:\n")
        for pair, names in unanswered.items():
            print(f"- `{pair}`: {', '.join(names)}")


if __name__ == "__main__":
    main()
