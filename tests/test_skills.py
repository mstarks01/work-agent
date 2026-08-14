"""Tests for skill loading, section extraction, digest assembly, and
node skill-text composition."""

import dataclasses

import pytest

from stride_service.frameworks import LANE_SECTION_HEADINGS
from stride_service.frameworks.stride import STRIDE
from stride_service.frameworks.stride.record import STRIDE_CATEGORIES, Claim
from stride_service.markdown_loader import (
    MarkdownFormatError,
    MarkdownLoader,
    MarkdownNotFoundError,
)
from stride_service.skills import (
    compose_critic_skills,
    compose_domain_skills,
    compose_lane_skills,
    estimate_tokens,
    extract_section,
    lane_boundary_digest,
    lane_skill_doc,
    split_sections,
)


def skill_text(title, scope="The lane definition."):
    sections = "\n\n".join(
        f"## {heading}\n\n{scope if heading == 'Scope' else f'{heading} body.'}"
        for heading in LANE_SECTION_HEADINGS
    )
    return f"# {title}\n\n{sections}\n"


@pytest.fixture
def package_root(tmp_path):
    """A package's own text root, laid out the way the gate expects one.

    ``lanes/<lane>/{skill,exemplars}.md`` plus the three package-level documents.
    Built rather than pointed at the shipped tree so a test can break one file
    without editing the repository's own.
    """
    root = tmp_path / "stride"
    for lane in STRIDE_CATEGORIES:
        lane_dir = root / "lanes" / lane
        lane_dir.mkdir(parents=True)
        (lane_dir / "skill.md").write_text(
            skill_text(lane, scope=f"Lane boundary for {lane}.")
        )
        (lane_dir / "exemplars.md").write_text(f"# {lane} exemplars\n\nA draft.\n")
    (root / "severity_rubric.md").write_text("# Severity Rubric\n\nRate both.\n")
    (root / "critic.md").write_text("# Critic\n\nWhat confirmed asserts.\n")
    (root / "disclaimer.md").write_text("AI-generated.\n")
    return root


@pytest.fixture
def domains_root(tmp_path):
    """The shared pack root, which is nobody's package."""
    root = tmp_path / "domains"
    root.mkdir()
    (root / "web.md").write_text("# Web Pack\n\nWeb patterns.\n")
    return root


def grades_nothing():
    """A package whose record carries no ``severity``.

    Composition asks the *record* rather than a declared flag, so this is built
    by narrowing the record rather than by setting one — the same question the
    gate asks when it refuses a rubric nothing would read.
    """
    return dataclasses.replace(STRIDE, record=Claim)


class TestLoadingSkillFiles:
    def test_load_returns_file_text(self, domains_root):
        assert MarkdownLoader(domains_root).load("web") == (
            "# Web Pack\n\nWeb patterns.\n"
        )

    def test_names_lists_relative_names_sorted(self, package_root):
        names = MarkdownLoader(package_root).names()
        assert names == sorted(names)
        assert "lanes/spoofing/skill" in names
        assert "severity_rubric" in names
        # Two per lane, plus the three package-level documents.
        assert len(names) == 2 * len(STRIDE_CATEGORIES) + 3

    def test_unknown_name_raises(self, package_root):
        with pytest.raises(MarkdownNotFoundError):
            MarkdownLoader(package_root).load("lanes/nonexistent/skill")

    def test_traversal_outside_root_raises(self, package_root):
        (package_root.parent / "outside.md").write_text("secret")
        with pytest.raises(MarkdownNotFoundError):
            MarkdownLoader(package_root).load("../outside")

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            MarkdownLoader(tmp_path / "absent")


class TestSectionParsing:
    def test_split_preserves_order_and_bodies(self):
        sections = split_sections(skill_text("Spoofing"))
        assert list(sections) == list(LANE_SECTION_HEADINGS)
        assert sections["Guardrails"] == "Guardrails body."

    def test_headings_are_taken_verbatim(self):
        sections = split_sections("## Scope \n\nbody\n")
        assert list(sections) == ["Scope "]

    def test_duplicate_heading_raises(self):
        with pytest.raises(MarkdownFormatError, match="duplicate"):
            split_sections("## Scope\n\na\n\n## Scope\n\nb\n")

    def test_extract_returns_section_body(self):
        text = skill_text("Tampering", scope="Integrity lane.")
        assert extract_section(text, "Scope") == "Integrity lane."

    def test_extract_missing_section_raises(self):
        with pytest.raises(MarkdownFormatError, match="missing section '## Scope'"):
            extract_section("# Title\n\n## Applicability\n\nbody\n", "Scope")

    def test_extract_empty_section_raises(self):
        with pytest.raises(MarkdownFormatError, match="empty"):
            extract_section("## Scope\n\n## Applicability\n\nbody\n", "Scope")


class TestBoundaryDigest:
    def test_digest_holds_all_scopes_in_the_packages_declared_order(self, package_root):
        digest = lane_boundary_digest(MarkdownLoader(package_root), STRIDE)
        assert digest.startswith("# STRIDE Lane Boundaries")
        positions = [
            digest.index(f"Lane boundary for {lane}.") for lane in STRIDE.lanes
        ]
        assert positions == sorted(positions)
        assert "## Information Disclosure" in digest

    def test_the_digest_is_one_packages_lanes_and_no_others(self, package_root):
        """What makes a cross-framework merge unavailable rather than discouraged.

        A critic dedupes against the lane definitions its own lane agents used.
        Assembling the digest from the package's own declaration is what keeps a
        second framework's lanes out of it, whatever the tree happens to hold.
        """
        two_lanes = dataclasses.replace(STRIDE, lanes=("spoofing", "tampering"))
        digest = lane_boundary_digest(MarkdownLoader(package_root), two_lanes)
        assert "Lane boundary for repudiation." not in digest

    def test_digest_excludes_other_sections(self, package_root):
        digest = lane_boundary_digest(MarkdownLoader(package_root), STRIDE)
        assert "Mitigations body." not in digest

    def test_malformed_lane_skill_fails_digest(self, package_root):
        bad = package_root / "lanes" / "tampering" / "skill.md"
        bad.write_text("# Tampering\n\n## Threat Patterns\n\nbody\n")
        with pytest.raises(MarkdownFormatError, match="missing section '## Scope'"):
            lane_boundary_digest(MarkdownLoader(package_root), STRIDE)

    def test_missing_lane_skill_fails_digest(self, package_root):
        (package_root / "lanes" / "repudiation" / "skill.md").unlink()
        with pytest.raises(MarkdownNotFoundError):
            lane_boundary_digest(MarkdownLoader(package_root), STRIDE)


class TestComposition:
    def test_lane_skills_are_the_lane_then_the_rubric(self, package_root):
        text = compose_lane_skills(MarkdownLoader(package_root), STRIDE, "spoofing")
        assert text.index("Lane boundary for spoofing.") < text.index(
            "# Severity Rubric"
        )

    def test_a_package_that_grades_nothing_composes_no_rubric(self, package_root):
        """The rubric is present exactly when the record carries a severity."""
        text = compose_lane_skills(
            MarkdownLoader(package_root), grades_nothing(), "spoofing"
        )
        assert "# Severity Rubric" not in text
        assert "Lane boundary for spoofing." in text

    def test_lane_skills_never_carry_packs(self, package_root, domains_root):
        """Packs are per-job, so they ride in state rather than the instruction."""
        text = compose_lane_skills(MarkdownLoader(package_root), STRIDE, "spoofing")
        assert "# Web Pack" not in text

    def test_domain_skills_compose_in_selection_order(self, domains_root):
        text = compose_domain_skills(MarkdownLoader(domains_root), ("web",))
        assert text.startswith("# Web Pack")

    def test_no_packs_composes_to_empty(self, domains_root):
        assert compose_domain_skills(MarkdownLoader(domains_root), ()) == ""

    def test_unknown_pack_raises(self, domains_root):
        with pytest.raises(MarkdownNotFoundError):
            compose_domain_skills(MarkdownLoader(domains_root), ("mainframe",))

    def test_critic_gets_rubric_then_package_critic_then_digest(self, package_root):
        text = compose_critic_skills(MarkdownLoader(package_root), STRIDE)
        assert (
            text.index("# Severity Rubric")
            < text.index("# Critic")
            < text.index("# STRIDE Lane Boundaries")
        )
        assert "Mitigations body." not in text
        assert "# Web Pack" not in text

    def test_the_lane_skill_name_is_the_packages_own_layout(self):
        """One spelling of ``lanes/<lane>/skill``, shared by the gate and here."""
        assert lane_skill_doc("spoofing") == "lanes/spoofing/skill"


class TestTokenEstimate:
    def test_rounds_up_words_times_four_thirds(self):
        assert estimate_tokens("one two three") == 4
        assert estimate_tokens("") == 0
