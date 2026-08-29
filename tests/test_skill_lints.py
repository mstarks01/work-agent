"""CI lints over the real Markdown trees the graph composes instructions from.

Two roots, because the cutover split one. A lane skill and the severity rubric
are **STRIDE's**, under ``frameworks/stride/``, since a lane is a package's own
partition of its subject and the rubric grades that package's own record. A
domain pack is **shared**, under ``domains/``, since it describes a technology
rather than a framework and every carried framework's lanes may earn it. That
split is ADR 0011's rule — a document's home follows its retrieval key — and
these lints are where it is enforced against the tree rather than argued about.

The package gate (:func:`~analysis_service.frameworks.validate_package`) already
refuses a package whose lanes are missing files or whose ``## Scope`` headings
have drifted. What is here is what the gate does not reach: the token caps, the
full heading set, and the shared root's own contract.
"""

from pathlib import Path

import pytest

from analysis_service.domains import DETECTORS
from analysis_service.frameworks import LANE_SECTION_HEADINGS, PACKAGES
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.skills import (
    estimate_tokens,
    lane_boundary_digest,
    lane_skill_doc,
    split_sections,
)
from analysis_service.token_caps import TOKEN_CAPS, covered_assets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRAMEWORKS_DIR = PROJECT_ROOT / "frameworks"
DOMAINS_DIR = PROJECT_ROOT / "domains"

# Every capped file of every registered package, resolved to its cap's key.
# Collected once, and parametrized over, so a package ``PACKAGES`` names cannot
# have text no lint reads — which is exactly what ASVS's seventeen lane skills
# and its lane digest had while the caps were a constant per kind and the lints
# below walked ``frameworks/stride`` alone.
PACKAGE_ASSETS = sorted(covered_assets(FRAMEWORKS_DIR))

#: One loader per registered package, and every ``(framework, lane)`` pair whose
#: skill ships. What the lane lints below parametrize over.
#:
#: **The same widening #280 made to the exemplar lints.** A lane skill states one
#: package's subject, but every rule below is a rule about what a *skill* may be —
#: its section shape, its non-emptiness, what it may tell a model about its own
#: input — and those hold for every package that ships one.
PACKAGE_LOADERS = {name: MarkdownLoader(FRAMEWORKS_DIR / name) for name in PACKAGES}
LANE_SKILLS = [
    (name, lane) for name, package in PACKAGES.items() for lane in package.lanes
]

# A domain pack's three fixed H2 sections, in order. Fewer than a lane skill's
# five because a pack does not define a lane or a rating — it says when it
# applies, what to ask, and what it may not be used for.
PACK_SECTION_HEADINGS = ("When this applies", "What to look for", "Guardrails")

domain_loader = MarkdownLoader(DOMAINS_DIR)
domain_packs = sorted(domain_loader.names())


@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_lane_directories_match_the_packages_declared_lanes(framework):
    """The tree and the declaration are one list, in one order."""
    dirs = sorted(
        path.name for path in (FRAMEWORKS_DIR / framework / "lanes").iterdir()
    )
    assert dirs == sorted(PACKAGES[framework].lanes)


@pytest.mark.parametrize("framework,lane", LANE_SKILLS)
def test_lane_skill_has_exact_fixed_headings_in_order(framework, lane):
    """Duplicate of the package gate's own check, and kept deliberately.

    :func:`~analysis_service.frameworks.validate_package` refuses a package whose
    lane headings have drifted, so this cannot fail alone. What it buys is that
    the failure names the lane at collection time rather than at deployment
    construction, which is where a maintainer editing a skill is looking.
    """
    sections = split_sections(PACKAGE_LOADERS[framework].load(lane_skill_doc(lane)))
    assert list(sections) == list(LANE_SECTION_HEADINGS)


@pytest.mark.parametrize("framework,lane", LANE_SKILLS)
def test_lane_skill_sections_are_nonempty(framework, lane):
    """The half the gate does not reach.

    The gate compares the *headings* and stops. A section with the right
    heading and no body under it passes every check the deployment runs, and
    ships a lane whose ``## Mitigations`` tells its agent nothing.
    """
    sections = split_sections(PACKAGE_LOADERS[framework].load(lane_skill_doc(lane)))
    empty = [heading for heading, body in sections.items() if not body]
    assert not empty


@pytest.mark.parametrize("path,key", PACKAGE_ASSETS, ids=lambda value: str(value))
def test_package_asset_within_token_cap(path, key):
    """The alarm, over every registered package's static text.

    Every framework's, not STRIDE's: a lane skill states one package's subject,
    and how long a chapter of a published standard runs is a fact about that
    package rather than about the framework that happened to arrive first.
    """
    assert estimate_tokens(path.read_text(encoding="utf-8")) <= TOKEN_CAPS[key]


def test_every_package_asset_cap_still_alarms():
    """A cap more than twice the largest file it watches measures nothing.

    Checked over the largest asset of each kind rather than per file, because a
    cap covers a kind: STRIDE's 62-token disclaimer must not force down the cap
    that also has to fit ASVS's.
    """
    largest: dict[str, int] = {}
    for path, key in PACKAGE_ASSETS:
        tokens = estimate_tokens(path.read_text(encoding="utf-8"))
        largest[key] = max(largest.get(key, 0), tokens)
    dead = {
        key: TOKEN_CAPS[key]
        for key, size in largest.items()
        if TOKEN_CAPS[key] > 2 * size
    }
    assert not dead


@pytest.mark.parametrize("pack", domain_packs)
def test_domain_pack_within_token_cap(pack):
    assert estimate_tokens(domain_loader.load(pack)) <= TOKEN_CAPS["domain/pack"]


@pytest.mark.parametrize("pack", domain_packs)
def test_domain_pack_has_the_three_fixed_headings_in_order(pack):
    assert list(split_sections(domain_loader.load(pack))) == list(PACK_SECTION_HEADINGS)


@pytest.mark.parametrize("pack", domain_packs)
def test_domain_pack_says_it_is_not_evidence(pack):
    """The one line a pack cannot be shipped without.

    A pack is generative security knowledge that arrives in the same request as
    the submitter's own words, and the failure it could cause — a finding
    grounded in what the pack said rather than in what the system is — is the
    one this service's whole provenance model exists to prevent. Wording is the
    author's; carrying the claim is not optional.
    """
    guardrails = split_sections(domain_loader.load(pack))["Guardrails"]
    assert "not evidence" in guardrails.lower()


def test_every_shipped_pack_can_be_selected():
    """A pack no detector names is a file nothing can ever load."""
    assert set(domain_packs) == set(DETECTORS)


def test_the_shared_domains_root_carries_nothing_but_packs():
    """No framework's text leaked back into the root every framework reads.

    The package's own root is covered by the gate, which refuses unread
    Markdown under it. This is the other direction, and it is the one the
    cutover could plausibly get wrong: ``domains/`` was carved out of the old
    ``skills/`` tree, and a lane skill left behind here would be loadable by
    every framework rather than by the one that wrote it.
    """
    entries = [path for path in DOMAINS_DIR.iterdir() if not path.name.startswith(".")]
    assert all(path.suffix == ".md" for path in entries)


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_boundary_digest_assembles_within_cap(name):
    """The digest is assembled, so no file's own cap bounds it.

    One ``## Scope`` section growing is invisible to the lane-skill alarm — the
    skill has room — and lands here, on the text every one of that package's
    critics reads. Parametrized over ``PACKAGES`` because a digest is one per
    package and its size follows how many lanes that package declares: ASVS's
    seventeen lanes make a digest ASVS's own alarm has to watch.
    """
    package = PACKAGES[name]
    loader = MarkdownLoader(FRAMEWORKS_DIR / name)
    digest = lane_boundary_digest(loader, package)
    assert estimate_tokens(digest) <= TOKEN_CAPS["package/lane_digest"]


# There is no mechanically-pre-filtered element view: every lane agent receives
# the whole System Model, and ``## Applicability`` scopes only where a claim may
# be *filed*. A skill that claims its input was filtered is telling the model
# something false about what it is looking at.
@pytest.mark.parametrize("framework,lane", LANE_SKILLS)
def test_applicability_does_not_claim_a_filtered_element_view(framework, lane):
    """Every package's, because the fact it protects is the graph's.

    That a lane agent receives the whole System Model is a property of how the
    fan-out is built, not of the framework being fanned out — so a skill making
    the opposite claim is telling its model something false whichever package
    wrote it.
    """
    sections = split_sections(PACKAGE_LOADERS[framework].load(lane_skill_doc(lane)))
    assert "pre-filter" not in sections["Applicability"].lower()
