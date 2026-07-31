"""The build-time supported-param gate, probed directly against litellm.

``litellm.utils.get_optional_params`` is not a documented public API, and it is
what stands between a misconfigured tier and a mid-job raise after earlier nodes
have been paid for. So these are deliberately *probes*, not mocks: they assert
the behaviour of the installed ``litellm`` itself, and a version bump that moves
any of it must show up here rather than in production.

Every case below is a fact one of the map's decisions rests on. They are grouped
by which decision would be falsified if the assertion failed.
"""

from __future__ import annotations

import os
import sys

import pytest

from stride_service.model_gate import (
    ModelGateError,
    assert_kwarg_supported,
    check_supported,
)
from stride_service.vendors import vendor_for

GEMINI = "gemini-2.5-pro"
VERTEX_CLAUDE = "claude-sonnet-4-5@20250929"
ANTHROPIC_CLAUDE = "claude-sonnet-4-5-20250929"


def gate(vendor: str, model: str, **params) -> None:
    check_supported(vendor_for(vendor), model, params, source="probe")


class TestVertexIsNotOneProvider:
    """#12: the fact that killed ``Vendor.supported: frozenset[str]``.

    A single ``vertex`` entry cannot carry one supported set, because LiteLLM
    dispatches on the model-string prefix to different config classes —
    ``vertex_ai/claude-*`` subclasses ``AnthropicConfig`` and has no ``seed``,
    while ``vertex_ai/gemini-*`` does.
    """

    def test_vertex_gemini_accepts_seed(self):
        gate("vertex", GEMINI, temperature=0.0, seed=7)

    def test_vertex_claude_rejects_seed(self):
        with pytest.raises(ModelGateError):
            gate("vertex", VERTEX_CLAUDE, temperature=0.0, seed=7)

    def test_anthropic_direct_rejects_seed(self):
        with pytest.raises(ModelGateError):
            gate("anthropic", ANTHROPIC_CLAUDE, temperature=0.0, seed=7)


class TestValueConstraintsAreCaught:
    """Why the gate is ``get_optional_params``, not the name list.

    ``get_supported_openai_params`` is merely this call's *input*. Only the full
    call runs ``_map_openai_params`` as well as ``_check_valid_arg``, which is
    what catches a constraint set membership cannot express.
    """

    def test_o_series_rejects_greedy_decoding(self):
        # Both tiers pin temperature = 0.0, so this is how "o-series cannot
        # judge" fails closed rather than surprising a sweep.
        with pytest.raises(ModelGateError):
            gate("openai", "o3", temperature=0.0)

    def test_o_series_accepts_its_one_legal_temperature(self):
        gate("openai", "o3", temperature=1.0)


class TestWhatTheGateCannotDo:
    """The two holes, both closed elsewhere rather than left open."""

    def test_reasoning_effort_values_are_not_validated(self):
        # PASSES — which is why sampling.py's Literal is the real check. If this
        # ever starts raising, that Literal becomes belt-and-braces rather than
        # the only thing standing between config and a silent wrong.
        gate("openai", "o3", reasoning_effort="banana")

    def test_top_k_is_not_a_parameter_it_knows_about(self):
        # Absent from the signature entirely, so it cannot be checked at all —
        # which is why top_k left the config surface in sampling version 3.
        from litellm.utils import get_optional_params

        assert (
            "top_k"
            not in get_optional_params.__code__.co_varnames[
                : get_optional_params.__code__.co_argcount
            ]
        )

    def test_an_unknown_model_is_not_an_existence_check(self):
        # Falls back to the provider's base config rather than raising: the
        # gate is not a build-time existence check.
        gate("vertex", "gemini-9.9-imaginary", temperature=0.0)


class TestReasoningReachesEveryVendor:
    """The reasoning enum is uniform, so no per-vendor budget range is needed."""

    @pytest.mark.parametrize("effort", ["low", "medium", "high"])
    def test_gemini_accepts_the_effort_enum(self, effort):
        gate("vertex", GEMINI, temperature=0.0, reasoning_effort=effort)

    def test_anthropic_accepts_the_effort_enum(self):
        gate("anthropic", ANTHROPIC_CLAUDE, temperature=0.0, reasoning_effort="low")

    def test_a_non_reasoning_model_rejects_it(self):
        with pytest.raises(ModelGateError):
            gate("openai", "gpt-4o", reasoning_effort="low")

    def test_gemini_accepts_none_which_is_why_off_was_dropped(self):
        # PASSES the gate as thinkingBudget: 0, then 400s at request time. The
        # gate cannot catch it and no per-(vendor, model) data remains to, so
        # "off" was removed from the config surface instead of managed.
        gate("vertex", GEMINI, temperature=0.0, reasoning_effort="none")


class TestKwargAssertion:
    """``num_retries`` is ``**kwargs``-only, so a misspelling is silent."""

    def test_a_real_litellm_param_passes(self):
        assert_kwarg_supported("num_retries")

    def test_a_misspelling_fails_closed(self):
        # ADK forwards **kwargs verbatim, so without this a typo would silently
        # revert retry to a single try — the failure retry exists to prevent.
        with pytest.raises(ModelGateError, match="not a litellm parameter"):
            assert_kwarg_supported("num_retrys")


class TestHermeticImport:
    """The version pin alone pins nothing without the local cost map."""

    def test_the_local_cost_map_is_pinned(self):
        # litellm fetches its model-cost map from GitHub at import, and that map
        # backs the gate's own conditionals — so the gate's verdict would
        # otherwise depend on a network fetch at process start.
        assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"

    def test_litellm_is_imported_only_after_the_pin(self):
        # The guard fires if anything imported litellm first, so this asserts
        # the ordering held for this process rather than merely that the
        # variable is set now.
        assert "litellm" in sys.modules
        assert "stride_service.model_gate" in sys.modules


class TestErrorsPointAtTheKnob:
    def test_the_source_names_the_tier(self):
        with pytest.raises(ModelGateError, match="tiers.strong"):
            check_supported(
                vendor_for("anthropic"),
                ANTHROPIC_CLAUDE,
                {"seed": 7},
                source="tiers.strong",
            )


@pytest.mark.parametrize(
    ("vendor", "base_model", "strong_model"),
    [
        ("vertex", "gemini-2.5-flash", "gemini-2.5-pro"),
        ("anthropic", "claude-haiku-4-5-20251001", "claude-sonnet-4-5-20250929"),
        ("openai", "gpt-4.1-mini", "gpt-4.1"),
    ],
)
def test_every_documented_vendor_passes_the_gate_on_shipped_sampling(
    vendor, base_model, strong_model
):
    """What the docs offer is what the gate accepts — for all three, not one.

    The predecessor asserted this of the *shipped selection*, which worked only
    while a selection shipped. Now that none does, the equivalent guarantee has
    to be made of every pair `docs/First-Run.md` step 2 tells a reader to write
    down: shipped sampling has to survive whichever of them they pick, and a
    param one vendor rejects must not reach a reader as a working example.
    """
    from pathlib import Path

    from stride_service.model_tiers import load_model_tiers
    from stride_service.sampling import load_sampling

    root = Path(__file__).resolve().parents[1]
    tiers = load_model_tiers(
        root / "config" / "model_tiers.toml",
        env={
            "STRIDE_MODEL_BASE_VENDOR": vendor,
            "STRIDE_MODEL_BASE_MODEL": base_model,
            "STRIDE_MODEL_STRONG_VENDOR": vendor,
            "STRIDE_MODEL_STRONG_MODEL": strong_model,
        },
    )
    sampling = load_sampling(root / "config" / "sampling.toml", env={})
    for tier in ("base", "strong"):
        selection = tiers.tiers[tier]
        check_supported(
            selection.vendor_entry,
            selection.model,
            sampling.for_tier(tier).gate_params(),
            source=f"tiers.{tier}",
        )
