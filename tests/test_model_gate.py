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

import contextlib
import os
import re
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from analysis_service.conformance import REFERENCE_MODELS
from analysis_service.model_gate import (
    ModelGateError,
    assert_kwarg_supported,
    check_supported,
    output_ceiling,
)
from analysis_service.vendors import vendor_for

GEMINI = "gemini-2.5-pro"
VERTEX_CLAUDE = "claude-sonnet-4-6"
ANTHROPIC_CLAUDE = "claude-sonnet-4-6"


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
        # judge" failed closed rather than surprising a sweep, back when one ran.
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


class TestOutputCeiling:
    """What the cost map says each model will actually produce.

    The sampling caps are sized against measured output and then checked against
    these numbers at build time, so a map that moves under a version bump has to
    surface here rather than as a 400 on node one.
    """

    def test_the_supported_param_gate_does_not_see_an_over_ceiling_ask(self):
        # The whole reason this is a separate check: gpt-4o serves 16,384, and
        # asking for twice that is a well-formed request the gate waves through.
        gate("openai", "gpt-4o", max_output_tokens=32768)

    @pytest.mark.parametrize(
        ("vendor", "model", "ceiling"),
        [
            ("openai", "gpt-4o", 16384),
            ("anthropic", ANTHROPIC_CLAUDE, 64000),
            ("vertex", GEMINI, 65535),
        ],
    )
    def test_a_known_model_publishes_its_ceiling(self, vendor, model, ceiling):
        assert output_ceiling(vendor_for(vendor), model) == ceiling

    def test_an_unknown_model_is_not_gated(self):
        # The same open-world residual check_supported carries: refusing to run
        # a model the pinned map has not caught up with is the worse failure.
        assert output_ceiling(vendor_for("openai"), "gpt-6-unreleased") is None

    def test_an_unmapped_model_raises_a_bare_exception(self):
        # Why output_ceiling catches `Exception` rather than something narrower.
        # If a version bump starts raising a real type, this fails and the catch
        # can be tightened.
        from analysis_service.model_gate import _litellm

        with pytest.raises(Exception) as excinfo:
            _litellm.get_model_info(
                model="gpt-6-unreleased", custom_llm_provider="openai"
            )

        assert type(excinfo.value) is Exception


class TestRetryLayering:
    """What one ``attempt`` actually costs in HTTP requests.

    ``config/resilience.toml`` bounds LiteLLM's retry layer, and LiteLLM builds
    the provider SDK's client with a retry layer of its own set *from* that
    bound. The product is what a per-minute request quota sees, so it is a fact
    about the installed library and belongs in a probe: a version bump that
    changes the coupling — in either direction — has to show up here rather than
    as a rate-limited job.

    Probed on the OpenAI path because that is where LiteLLM plumbs
    ``max_retries`` through at all; no request is made, since the client is
    intercepted as it is built.
    """

    @staticmethod
    def _client_retries(monkeypatch, **kwargs) -> list[int]:
        """The SDK ``max_retries`` of each client one acompletion would build."""
        import asyncio

        from litellm.llms.openai.openai import OpenAIChatCompletion

        from analysis_service.model_gate import _litellm

        built: list[int] = []

        def intercept(self, **client_kwargs):
            built.append(client_kwargs.get("max_retries"))
            raise RuntimeError("intercepted before any request")

        monkeypatch.setattr(OpenAIChatCompletion, "_get_openai_client", intercept)

        async def attempt() -> None:
            with contextlib.suppress(Exception):
                await _litellm.acompletion(
                    model="openai/gpt-4o",
                    messages=[{"role": "user", "content": "probe"}],
                    api_key="not-a-real-key",
                    **kwargs,
                )

        asyncio.run(attempt())
        return built

    def test_the_first_attempt_carries_the_sdks_own_retries(self, monkeypatch):
        # num_retries=2 is the shipped attempts=3. LiteLLM makes three attempts,
        # and hands the first one an SDK client that will itself retry twice.
        built = self._client_retries(monkeypatch, num_retries=2)

        assert built == [2, 0, 0]
        assert sum(1 + retries for retries in built) == 5  # 2 * attempts - 1

    def test_max_retries_cannot_close_it_from_the_adapter(self, monkeypatch):
        # The reason resilience.py documents this rather than pinning it in
        # binding.py: num_retries overwrites an explicit max_retries on its way
        # to the client, so the kwarg would be a knob connected to nothing.
        built = self._client_retries(monkeypatch, num_retries=2, max_retries=0)

        assert built[0] == 2

    def test_a_single_attempt_costs_a_single_request(self, monkeypatch):
        # attempts=1 is the mid-incident floor, and it is a real floor: with
        # nothing to retry at either layer it is one request per node.
        assert self._client_retries(monkeypatch, num_retries=0) == [0]


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
        assert "analysis_service.model_gate" in sys.modules


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
    [(vendor, *models) for vendor, models in REFERENCE_MODELS.items()],
)
def test_every_documented_vendor_passes_the_gate_on_shipped_sampling(
    vendor, base_model, strong_model
):
    """What the docs offer is what the gate accepts — for every vendor, not one.

    The predecessor asserted this of the *shipped selection*, which worked only
    while a selection shipped. Now that none does, the equivalent guarantee has
    to be made of every pair `docs/First-Run.md` step 2 tells a reader to write
    down: shipped sampling has to survive whichever of them they pick, and a
    param one vendor rejects must not reach a reader as a working example.

    **Driven from `REFERENCE_MODELS`, because the hand-written list drifted.**
    It said `openai` was `gpt-4.1-mini` / `gpt-4.1` while the table it names
    said `gpt-4o` / `gpt-5.6`, and it carried no `bedrock` row at all — so the
    vendor added most recently was the one this check never made. A list that
    mirrors a table by hand is a second reader of it, and the two disagreed for
    as long as nobody read them side by side.
    """
    from pathlib import Path

    from analysis_service.sampling import load_sampling
    from tests.factories import tiers_for

    root = Path(__file__).resolve().parents[1]
    tiers = tiers_for(vendor)
    assert (tiers.tiers["base"].model, tiers.tiers["strong"].model) == (
        base_model,
        strong_model,
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


def test_every_local_only_switch_litellm_defines_is_set():
    """The pin does not pin this, so the suite has to.

    Each `LITELLM_LOCAL_*` variable turns off a remote config fetch. The
    repository set one of the four, and 1.97.0 fetches the Anthropic beta
    headers behind another of them -- at request time, from a URL whose content
    chooses an outgoing header, checked only for being a non-empty dict.

    Read out of litellm's own source rather than restated here, so a version
    bump that adds a fifth switch fails this test instead of opening an egress
    nobody looked for.
    """
    import litellm

    from analysis_service.model_gate import LITELLM_LOCAL_SWITCHES

    root = Path(litellm.__file__).parent
    defined = set()
    for source in root.rglob("*.py"):
        text = source.read_text(encoding="utf-8", errors="ignore")
        defined |= set(re.findall(r"LITELLM_LOCAL_[A-Z_]+", text))

    assert defined, "found no switches at all; the scan itself is broken"
    assert defined <= set(LITELLM_LOCAL_SWITCHES), (
        f"litellm defines switches this service does not set:"
        f" {sorted(defined - set(LITELLM_LOCAL_SWITCHES))}"
    )
    for switch in LITELLM_LOCAL_SWITCHES:
        assert os.environ.get(switch) == "True"


class TestTheBedrockFormRuleAgainstThePinnedMap:
    """The one form rule whose family is broad, measured against a real catalogue.

    A shape rule is only as good as the identifiers it meets, and Bedrock is the
    first vendor whose catalogue is large enough to say something. So this
    partitions every Bedrock key in the pinned cost map and asserts where each
    one lands. The **partition** is asserted rather than the counts: a litellm
    bump adds and retires models, and a test that pinned the numbers would fail
    on a routine dependency update rather than on a defect.
    """

    #: Every Bedrock key the pinned map carries, in this service's own
    #: spelling. `bedrock/`-prefixed keys are dropped because they are router
    #: strings rather than model identifiers, and that is what `Vendor.route`
    #: composes.
    @staticmethod
    def keys() -> list[str]:
        import litellm

        return [
            key
            for key, entry in litellm.model_cost.items()
            if entry.get("litellm_provider") in ("bedrock", "bedrock_converse")
            and not key.startswith("bedrock/")
        ]

    #: The identifiers the shape refuses although they reach the Claude family,
    #: each for a stated property rather than for its name. Listed so the
    #: partition below has somewhere to put them: a refusal nobody wrote down
    #: is indistinguishable from a defect.
    REFUSED: ClassVar[frozenset[str]] = frozenset(
        {
            # Vertex's `@date` spelling, on one key against the rest.
            "anthropic.claude-haiku-4-5@20251001",
            # The 2023 names, which carry no family name and generation.
            "anthropic.claude-instant-v1",
            "anthropic.claude-v1",
            "anthropic.claude-v2:1",
            # A Claude that omits the family segment: it meets the broad family
            # and fails the strict shape, rather than passing unpinned.
            "claude-sonnet-4-5-20250929-v1:0",
        }
    )

    def test_the_scan_reads_a_real_catalogue(self):
        """A partition over an empty set passes every assertion below."""
        assert len(self.keys()) > 100

    def test_every_bedrock_key_lands_where_the_rule_says_it_does(self):
        """Each key pins, dies on the shared denylist, or is a written refusal.

        A fourth outcome is a defect: a model AWS publishes that this service
        would refuse to name, with no decision behind the refusal.
        """
        bedrock = vendor_for("bedrock")
        unexplained = []
        for key in self.keys():
            try:
                bedrock.validate_model(key, source="probe")
            except ValueError as exc:
                # The shared denylist runs for every family and refuses a
                # floating form; only a shape refusal is this rule's own.
                if "not pinned" in str(exc) and key not in self.REFUSED:
                    unexplained.append(key)

        assert not unexplained, (
            f"these Bedrock identifiers are refused and nothing here says why:"
            f" {sorted(unexplained)}. Either the shape is wrong, or the"
            " refusal is a decision that belongs in REFUSED with its reason."
        )

    def test_every_refused_identifier_is_still_in_the_catalogue(self):
        """A refusal nobody can reach excuses the next one that spells it.

        The bare-spelling entry is the exception: it is the shape a config
        copying an anthropic-direct row produces, so it is a refusal about
        something an operator writes rather than something AWS publishes.
        """
        published = set(self.keys()) | {"claude-sonnet-4-5-20250929-v1:0"}
        assert self.REFUSED <= published, (
            f"these refusals name identifiers the pinned map no longer carries:"
            f" {sorted(self.REFUSED - published)}"
        )

    def test_the_family_rule_catches_every_claude_and_nothing_else(self):
        from analysis_service.vendors import _BEDROCK_CLAUDE_RULE

        bedrock = vendor_for("bedrock")
        for key in self.keys():
            reaches_claude = bedrock._rule_for(key) is _BEDROCK_CLAUDE_RULE
            assert reaches_claude == ("claude-" in key), key

    def test_the_generation_parse_reads_every_pinned_claude(self):
        """The form rule and the parse must agree about what a Claude is.

        They are two readers of one question, so they are checked against each
        other rather than each against its own list. A pinned identifier the
        parse could not read would silence the temperature floor on exactly the
        models that floor exists for.
        """
        from analysis_service.vendors import _BEDROCK_CLAUDE_RULE, claude_generation

        bedrock = vendor_for("bedrock")
        unread = []
        for key in self.keys():
            if bedrock._rule_for(key) is not _BEDROCK_CLAUDE_RULE:
                continue
            try:
                bedrock.validate_model(key, source="probe")
            except ValueError:
                continue
            if claude_generation(key) is None:
                unread.append(key)
        assert not unread, (
            f"these identifiers pin as a Claude and parse to no generation:"
            f" {sorted(unread)}"
        )

    def test_no_non_claude_key_parses_as_a_claude(self):
        from analysis_service.vendors import claude_generation

        false_positives = [
            key
            for key in self.keys()
            if "claude-" not in key and claude_generation(key)
        ]
        assert not false_positives, false_positives
