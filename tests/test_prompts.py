"""Tests for prompt composition and the skill/markdown loader aliases."""

import pytest

from stride_service.markdown_loader import (
    MarkdownFormatError,
    MarkdownLoader,
    MarkdownNotFoundError,
)
from stride_service.prompts import (
    PROMPT_BODY_NAMES,
    PROMPT_SECTION_HEADINGS,
    compose_analyst_prompt,
    compose_critic_prompt,
    compose_extract_prompt,
    compose_repair_prompt,
    exemplar_name,
)
from stride_service.report import STRIDE_CATEGORIES
from stride_service.skills import SkillFormatError, SkillLoader, SkillNotFoundError


def prompt_text(title, body="Body."):
    sections = "\n\n".join(
        f"## {heading}\n\n{body}" for heading in PROMPT_SECTION_HEADINGS
    )
    return f"# {title}\n\n{sections}\n"


@pytest.fixture
def prompts_root(tmp_path):
    for name in PROMPT_BODY_NAMES:
        body = "Analyze as the {category} analyst." if name == "analyst" else "Body."
        (tmp_path / f"{name}.md").write_text(prompt_text(name, body))
    exemplars = tmp_path / "exemplars"
    exemplars.mkdir()
    for category in STRIDE_CATEGORIES:
        (exemplars / f"{category}.md").write_text(f"# {category}\n\nExemplars.\n")
    return tmp_path


@pytest.fixture
def loader(prompts_root):
    return MarkdownLoader(prompts_root)


class TestLoaderAliases:
    def test_skill_names_alias_the_one_implementation(self):
        assert SkillLoader is MarkdownLoader
        assert SkillNotFoundError is MarkdownNotFoundError
        assert SkillFormatError is MarkdownFormatError

    def test_prompts_load_through_the_same_loader(self, loader):
        assert "exemplars/spoofing" in loader.names()

    def test_missing_prompt_fails_closed(self, loader):
        with pytest.raises(MarkdownNotFoundError):
            loader.load("planner")

    def test_traversal_is_denied_as_absent(self, loader, tmp_path):
        (tmp_path.parent / "outside.md").write_text("secret")
        with pytest.raises(MarkdownNotFoundError):
            loader.load("../outside")


class TestComposeAnalystPrompt:
    def test_body_precedes_category_exemplars(self, loader):
        composed = compose_analyst_prompt(loader, "tampering")
        assert composed.index("## Role") < composed.index("Exemplars.")

    def test_only_the_requested_category_is_included(self, loader):
        composed = compose_analyst_prompt(loader, "tampering")
        assert "# tampering" in composed
        assert "# spoofing" not in composed

    def test_shared_body_is_identical_across_categories(self, loader):
        prefixes = {
            compose_analyst_prompt(loader, category).split("# ")[1]
            for category in STRIDE_CATEGORIES
        }
        assert len(prefixes) == 1

    def test_state_placeholders_survive_composition(self, loader):
        assert "{category}" in compose_analyst_prompt(loader, "spoofing")

    def test_missing_exemplar_file_fails_closed(self, loader, prompts_root):
        (prompts_root / "exemplars" / "spoofing.md").unlink()
        with pytest.raises(MarkdownNotFoundError):
            compose_analyst_prompt(loader, "spoofing")


class TestComposePeerPrompts:
    @pytest.mark.parametrize(
        ("compose", "name"),
        [
            (compose_critic_prompt, "critic"),
            (compose_extract_prompt, "extract"),
            (compose_repair_prompt, "repair"),
        ],
    )
    def test_body_is_returned_whole(self, loader, compose, name):
        composed = compose(loader)
        assert composed.startswith(f"# {name}")
        assert composed.endswith("\n")
        for heading in PROMPT_SECTION_HEADINGS:
            assert f"## {heading}" in composed

    def test_critic_gets_no_exemplars(self, loader):
        assert "Exemplars." not in compose_critic_prompt(loader)


def test_exemplar_name_is_root_relative(loader):
    assert exemplar_name("denial-of-service") == "exemplars/denial-of-service"
    assert loader.load(exemplar_name("denial-of-service"))
