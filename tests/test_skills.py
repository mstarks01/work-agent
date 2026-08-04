"""Tests for skill loading, section extraction, digest assembly, and
node skill-text composition."""

import pytest

from stride_service.markdown_loader import (
    MarkdownFormatError,
    MarkdownLoader,
    MarkdownNotFoundError,
)
from stride_service.skills import (
    SKILL_SECTION_HEADINGS,
    STRIDE_CATEGORIES,
    category_boundary_digest,
    compose_analyze_skills,
    compose_critic_skills,
    estimate_tokens,
    extract_section,
    split_sections,
)


def skill_text(title, scope="The lane definition."):
    sections = "\n\n".join(
        f"## {heading}\n\n{scope if heading == 'Scope' else f'{heading} body.'}"
        for heading in SKILL_SECTION_HEADINGS
    )
    return f"# {title}\n\n{sections}\n"


@pytest.fixture
def skills_root(tmp_path):
    stride = tmp_path / "stride"
    stride.mkdir()
    for category in STRIDE_CATEGORIES:
        text = skill_text(category, scope=f"Lane boundary for {category}.")
        (stride / f"{category}.md").write_text(text)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "severity_rubric.md").write_text("# Severity Rubric\n\nRate both.\n")
    domains = tmp_path / "domains"
    domains.mkdir()
    (domains / "web.md").write_text("# Web Pack\n\nWeb patterns.\n")
    return tmp_path


class TestLoadingSkillFiles:
    def test_load_returns_file_text(self, skills_root):
        loader = MarkdownLoader(skills_root)
        assert loader.load("domains/web") == "# Web Pack\n\nWeb patterns.\n"

    def test_names_lists_relative_names_sorted(self, skills_root):
        names = MarkdownLoader(skills_root).names()
        assert names[0] == "domains/web"
        assert "shared/severity_rubric" in names
        assert names == sorted(names)
        assert len(names) == 8

    def test_unknown_name_raises(self, skills_root):
        with pytest.raises(MarkdownNotFoundError):
            MarkdownLoader(skills_root).load("stride/nonexistent")

    def test_traversal_outside_root_raises(self, skills_root):
        (skills_root.parent / "outside.md").write_text("secret")
        with pytest.raises(MarkdownNotFoundError):
            MarkdownLoader(skills_root).load("../outside")

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            MarkdownLoader(tmp_path / "absent")


class TestSectionParsing:
    def test_split_preserves_order_and_bodies(self):
        sections = split_sections(skill_text("Spoofing"))
        assert list(sections) == list(SKILL_SECTION_HEADINGS)
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
    def test_digest_holds_all_scopes_in_canonical_order(self, skills_root):
        digest = category_boundary_digest(MarkdownLoader(skills_root))
        assert digest.startswith("# STRIDE Category Boundaries")
        positions = [
            digest.index(f"Lane boundary for {category}.")
            for category in STRIDE_CATEGORIES
        ]
        assert positions == sorted(positions)
        assert "## Information Disclosure" in digest

    def test_digest_excludes_other_sections(self, skills_root):
        digest = category_boundary_digest(MarkdownLoader(skills_root))
        assert "Mitigations body." not in digest

    def test_malformed_category_file_fails_digest(self, skills_root):
        bad = skills_root / "stride" / "tampering.md"
        bad.write_text("# Tampering\n\n## Threat Patterns\n\nbody\n")
        with pytest.raises(MarkdownFormatError, match="missing section '## Scope'"):
            category_boundary_digest(MarkdownLoader(skills_root))

    def test_missing_category_file_fails_digest(self, skills_root):
        (skills_root / "stride" / "repudiation.md").unlink()
        with pytest.raises(MarkdownNotFoundError):
            category_boundary_digest(MarkdownLoader(skills_root))


class TestComposition:
    def test_analyze_order_is_category_rubric_packs(self, skills_root):
        text = compose_analyze_skills(
            MarkdownLoader(skills_root), "spoofing", domain_packs=("web",)
        )
        assert (
            text.index("Lane boundary for spoofing.")
            < text.index("# Severity Rubric")
            < text.index("# Web Pack")
        )

    def test_analyze_without_packs_omits_them(self, skills_root):
        text = compose_analyze_skills(MarkdownLoader(skills_root), "spoofing")
        assert "# Web Pack" not in text

    def test_unknown_pack_raises(self, skills_root):
        with pytest.raises(MarkdownNotFoundError):
            compose_analyze_skills(
                MarkdownLoader(skills_root), "spoofing", domain_packs=("mainframe",)
            )

    def test_critic_gets_rubric_then_digest_only(self, skills_root):
        text = compose_critic_skills(MarkdownLoader(skills_root))
        assert text.index("# Severity Rubric") < text.index(
            "# STRIDE Category Boundaries"
        )
        assert "Mitigations body." not in text
        assert "# Web Pack" not in text


class TestTokenEstimate:
    def test_rounds_up_words_times_four_thirds(self):
        assert estimate_tokens("one two three") == 4
        assert estimate_tokens("") == 0
