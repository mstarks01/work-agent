"""The execution identity a certification decision is made against (#504)."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import ClassVar

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from analysis_service.conformance import REFERENCE_MODELS
from analysis_service.identity import (
    BUILD_DISTRIBUTIONS,
    IDENTITY_VERSION,
    BuildIdentityError,
    build_identity,
    execution_fingerprint,
    execution_identity,
    fingerprint,
    served_trust_for,
)
from analysis_service.sampling import TierSampling
from analysis_service.vendors import VENDOR_NAMES, Vendor, vendor_for

# Deliberately not the installed versions: a fixture that happened to match the
# running install would let a recomputation that ignores the recorded map pass.
BUILD = {"analysis-service": "0.0.1", "google-adk": "0.0.2", "litellm": "0.0.3"}
INSTRUCTIONS = "ab" * 32
OTHER_INSTRUCTIONS = "cd" * 32


def fp(**overrides: object) -> str:
    """A fingerprint over a fixed identity, with named parts replaced."""
    parts: dict = {
        "requested_route": "vertex_ai/gemini-2.5-pro",
        "served_route": "vertex_ai/gemini-2.5-pro-002",
        "sampling": TierSampling(temperature=0.0).model_dump(),
        "instruction_sha256": INSTRUCTIONS,
        "build": BUILD,
    }
    return execution_fingerprint(**{**parts, **overrides})


class TestShape:
    def test_is_a_sha256_hex_digest(self):
        assert re.fullmatch(r"[0-9a-f]{64}", fp())

    def test_is_deterministic(self):
        assert fp() == fp()

    def test_the_identity_carries_its_own_version(self):
        identity = execution_identity(
            requested_route="anthropic/claude-opus-5",
            served_route="anthropic/claude-opus-5",
            sampling={},
            instruction_sha256=INSTRUCTIONS,
            build=BUILD,
        )
        assert identity["version"] == IDENTITY_VERSION

    def test_the_identity_states_what_the_served_build_is_worth(self):
        # Inside the payload, not beside it: an identity that could really
        # verify a served build must hash differently from one that only
        # repeated the provider's claim.
        identity = execution_identity(
            requested_route="anthropic/claude-opus-5",
            served_route="anthropic/claude-opus-5",
            sampling={},
            instruction_sha256=INSTRUCTIONS,
            build=BUILD,
        )
        assert identity["served_trust"] == "provider_reported"

    def test_a_vendor_that_echoes_the_request_says_so(self):
        """The #606 defect. Every vertex fingerprint claimed evidence it had.

        ``SERVED_TRUST`` was the constant ``"provider_reported"``, and litellm
        fills the served identifier from the request on vertex — so the payload
        stated that a provider named the build, and none had.
        """
        identity = execution_identity(
            requested_route="vertex_ai/gemini-2.5-pro",
            served_route="vertex_ai/gemini-2.5-pro",
            sampling={},
            instruction_sha256=INSTRUCTIONS,
            build=BUILD,
        )
        assert identity["served_trust"] == "requested_echo"

    def test_the_two_vendors_hash_differently_on_the_same_pair(self):
        # The value is in the payload, so the same routes under two vendors
        # cannot collide on one fingerprint.
        assert fp(
            requested_route="vertex_ai/claude-opus-5",
            served_route="vertex_ai/claude-opus-5",
        ) != fp(
            requested_route="anthropic/claude-opus-5",
            served_route="anthropic/claude-opus-5",
        )

    def test_a_route_naming_no_vendor_raises_rather_than_guessing(self):
        # Inventing a vendor is how a fingerprint comes to name a provider
        # that never ran. The producer's own invariant makes this unreachable:
        # node_models is built from the tier config through Vendor.prefix.
        with pytest.raises(ValueError):
            served_trust_for("gemini-2.5-pro")
        with pytest.raises(ValueError):
            served_trust_for("cohere/command-r")

    def test_the_hash_follows_from_the_identity_a_reader_can_see(self):
        # Nothing is hashed that the identity mapping does not show, so a
        # fingerprint is recomputable from a recorded artifact.
        identity = execution_identity(
            requested_route="vertex_ai/gemini-2.5-pro",
            served_route="vertex_ai/gemini-2.5-pro-002",
            sampling=TierSampling(temperature=0.0).model_dump(),
            instruction_sha256=INSTRUCTIONS,
            build=BUILD,
        )
        assert fingerprint(identity) == fp()

    def test_nesting_keeps_a_sampling_param_from_colliding_with_a_field(self):
        # A flat payload would let a param named "served" overwrite the served
        # route, and a collision there reads as a matching identity.
        assert fp(sampling={"served": "vertex_ai/gemini-2.5-pro-002"}) != fp(
            sampling={}
        )


class TestEveryPartMoves:
    """Each criterion #504 lists, one test each: change it, the identity moves."""

    def test_a_moved_served_build(self):
        assert fp() != fp(served_route="vertex_ai/gemini-2.5-pro-003")

    def test_a_different_requested_route(self):
        # The threat this closes. A translator that answers an approved build
        # while the deployment asked for a cheaper one presents a pair no
        # manifest blessed, so the provider's word alone selects nothing.
        assert fp() != fp(requested_route="vertex_ai/gemini-2.5-flash")

    def test_the_same_served_build_under_two_vendors(self):
        # A served identifier carries no vendor, and Vertex-hosted Claude and
        # Anthropic-direct return through an identical transformation.
        assert fp(
            requested_route="vertex_ai/claude-opus-5",
            served_route="vertex_ai/claude-opus-5",
        ) != fp(
            requested_route="anthropic/claude-opus-5",
            served_route="anthropic/claude-opus-5",
        )

    def test_a_changed_sampling_param(self):
        assert fp() != fp(sampling=TierSampling(temperature=1.0).model_dump())

    def test_an_edited_prompt(self):
        assert fp() != fp(instruction_sha256=OTHER_INSTRUCTIONS)

    @pytest.mark.parametrize("distribution", BUILD_DISTRIBUTIONS)
    def test_a_bumped_distribution(self, distribution):
        # Every distribution between the node and the provider, not just the
        # translator: a service release changes the graph, and the runtime
        # decides what reaches the model.
        assert fp() != fp(build={**BUILD, distribution: "99.0.0"})


class TestRecomputability:
    def test_a_sampling_round_trip_reproduces_the_hash(self):
        # The report stores TierSampling.model_dump(); reconstructing from that
        # dict must reproduce the hash stamped on the NodeRun.
        sampling = TierSampling(temperature=0.0, seed=3, thinking="low")
        assert fp(sampling=sampling.model_dump()) == fp(
            sampling=TierSampling(**sampling.model_dump()).model_dump()
        )

    def test_a_recorded_build_map_is_used_rather_than_the_running_one(self):
        # A verifier hands in what the artifact recorded. If this read the
        # install instead, every stored sweep would fail the moment a
        # dependency moved — drift in the reader, not in the run.
        assert fp(build=BUILD) != fp(build=dict(build_identity()))


class TestBuildIdentity:
    def test_reports_every_distribution_it_names(self):
        assert set(build_identity()) == set(BUILD_DISTRIBUTIONS)

    def test_reports_versions_that_are_actually_installed(self):
        from importlib.metadata import version

        assert build_identity() == {name: version(name) for name in BUILD_DISTRIBUTIONS}

    def test_a_missing_distribution_fails_closed(self, monkeypatch):
        # An identity with a hole in it hashes the same before and after the
        # bump the field exists to catch, so it raises instead.
        build_identity.cache_clear()
        monkeypatch.setattr(
            "analysis_service.identity.BUILD_DISTRIBUTIONS",
            (*BUILD_DISTRIBUTIONS, "no-such-distribution"),
        )
        with pytest.raises(BuildIdentityError, match="no-such-distribution"):
            build_identity()
        build_identity.cache_clear()


def test_every_distribution_the_package_imports_is_declared():
    """A dependency you rely on but do not declare is one a bump can remove.

    `pyproject.toml` already records this happening once, to `cryptography`.
    It had happened twice more by the time an audit looked: `anyio`, `starlette`
    and `google-genai` were all imported by name and all arrived only
    transitively.

    The import name is not the distribution name -- `jwt` is PyJWT, `google` is
    three separate distributions -- so this asks `packages_distributions()`
    rather than guessing, and accepts a top-level name when ANY distribution
    providing it is declared.
    """
    import tomllib
    from importlib.metadata import packages_distributions

    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {
        re.split(r"[<>=\[]", spec)[0].strip().lower()
        for spec in project["project"]["dependencies"]
    }
    provides = packages_distributions()

    imported: set[str] = set()
    for source in (REPO_ROOT / "src" / "analysis_service").rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])

    assert "litellm" in imported, "the import scan found nothing; it is broken"
    assert "litellm" in declared, "the dependency read found nothing; it is broken"

    undeclared = sorted(
        name
        for name in imported - sys.stdlib_module_names - {"analysis_service"}
        if name in provides
        and not any(dist.lower() in declared for dist in provides[name])
    )

    assert not undeclared, (
        f"imported but not declared in pyproject.toml: {undeclared}"
        " — a transitive bump can remove these from under the code that calls them"
    )


class TestTheTableMatchesWhatTheTranslatorDoes:
    """``served_trust`` is a property of the vendor **and** of the translator.

    A litellm bump that started reading Gemini's ``modelVersion`` would make the
    ``vertex`` entry wrong, and every fingerprint would move on that bump anyway
    — ``litellm`` sits in ``BUILD_DISTRIBUTIONS`` — so the hashes would move for
    an unrelated reason and the stale entry would stay invisible.

    So the table is checked against what the installed translator does, rather
    than against a second copy of the same claim. Each vendor's own
    transformation is driven with a canned response naming a build **different**
    from the request, offline, with no credential and no network.
    """

    REQUESTED = "requested-build"
    SERVED = "served-build-002"

    #: One canned provider response per vendor, in that vendor's own wire
    #: shape, naming :attr:`SERVED` where that API carries a model name. Keyed
    #: by vendor, so a row cannot be added without answering here.
    #:
    #: The Bedrock body names it nowhere, and that absence is the fact: a
    #: Converse response carries no model identifier at all, so there is no
    #: field a translator could read and the served half can only echo the
    #: request. A body that invented one would test a wire shape AWS does not
    #: send.
    BODIES: ClassVar[dict[str, dict]] = {
        "vertex": {
            "candidates": [
                {
                    "content": {"parts": [{"text": "hi"}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            },
            # Gemini carries the served build here. litellm never reads it,
            # which is exactly what this test pins.
            "modelVersion": SERVED,
        },
        "anthropic": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": SERVED,
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
        "openai": {
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": SERVED,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hi"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
        "bedrock": {
            "output": {"message": {"role": "assistant", "content": [{"text": "hi"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        },
    }

    def test_every_vendor_has_a_canned_response(self):
        assert set(self.BODIES) == set(VENDOR_NAMES)

    def _served_name(self, vendor: Vendor) -> str:
        """What the installed translator puts in ``model_response.model``.

        The config class is resolved from the *model*, not from the vendor:
        ``vertex_ai/`` is not one provider, and litellm hands a Gemini
        identifier and a Llama identifier to different transformations.
        """
        import httpx
        from litellm.litellm_core_utils.litellm_logging import Logging
        from litellm.types.utils import LlmProviders, ModelResponse
        from litellm.utils import ProviderConfigManager

        model = REFERENCE_MODELS[vendor.name][0]
        config = ProviderConfigManager.get_provider_chat_config(
            model=model, provider=LlmProviders(vendor.litellm_provider)
        )
        assert config is not None, vendor.name
        raw = httpx.Response(
            200,
            json=self.BODIES[vendor.name],
            request=httpx.Request("POST", "https://provider.invalid/v1"),
        )
        logging_obj = Logging(
            model=model,
            messages=[],
            stream=False,
            call_type="completion",
            start_time=0,
            litellm_call_id="offline",
            function_id="offline",
        )
        logging_obj.optional_params = {}
        served = config.transform_response(
            model=self.REQUESTED,
            raw_response=raw,
            model_response=ModelResponse(),
            logging_obj=logging_obj,
            request_data={},
            messages=[{"role": "user", "content": "hi"}],
            optional_params={},
            litellm_params={},
            encoding=None,
        ).model
        assert served is not None, vendor.name
        return served

    @pytest.mark.parametrize("name", VENDOR_NAMES)
    def test_the_entry_matches_the_installed_transformation(self, name):
        vendor = vendor_for(name)
        served = self._served_name(vendor)
        if vendor.served_trust == "provider_reported":
            assert served == self.SERVED, (
                f"{name} is recorded as provider_reported, but the installed"
                " translator did not use the name in the response body"
            )
        else:
            assert served == self.REQUESTED, (
                f"{name} is recorded as requested_echo, but the installed"
                " translator read a name from the response body"
            )
