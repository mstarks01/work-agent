"""The execution identity a certification decision is made against (#504)."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from analysis_service.identity import (
    BUILD_DISTRIBUTIONS,
    IDENTITY_VERSION,
    SERVED_TRUST,
    BuildIdentityError,
    build_identity,
    execution_fingerprint,
    execution_identity,
    fingerprint,
)
from analysis_service.sampling import TierSampling

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
            requested_route="a",
            served_route="b",
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
            requested_route="a",
            served_route="b",
            sampling={},
            instruction_sha256=INSTRUCTIONS,
            build=BUILD,
        )
        assert identity["served_trust"] == SERVED_TRUST == "provider_reported"

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
