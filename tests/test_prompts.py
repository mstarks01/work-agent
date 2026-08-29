"""Tests for prompt composition and the shared Markdown loader."""

import pytest

from analysis_service.frameworks.stride.record import STRIDE_CATEGORIES
from analysis_service.markdown_loader import (
    MarkdownLoader,
    MarkdownNotFoundError,
)
from analysis_service.prompts import (
    PROMPT_BODY_NAMES,
    PROMPT_SECTION_HEADINGS,
    compose_analyze_prompt,
    compose_critic_prompt,
    compose_extract_prompt,
    compose_repair_prompt,
)
from analysis_service.skills import lane_exemplars_doc


def prompt_text(title, body="Body."):
    sections = "\n\n".join(
        f"## {heading}\n\n{body}" for heading in PROMPT_SECTION_HEADINGS
    )
    return f"# {title}\n\n{sections}\n"


@pytest.fixture
def prompts_root(tmp_path):
    """The shared root: one templated body per node, and no exemplars.

    A worked draft is written in one framework's own record shape, so it moved
    into that package's root and this one carries the bodies alone.
    """
    root = tmp_path / "prompts"
    root.mkdir()
    for name in PROMPT_BODY_NAMES:
        body = "Analyze as the {lane} agent." if name == "analyze" else "Body."
        (root / f"{name}.md").write_text(prompt_text(name, body))
    return root


@pytest.fixture
def package_root(tmp_path):
    """One package's root: its output contract, and its own lanes' exemplars."""
    root = tmp_path / "stride"
    root.mkdir(parents=True)
    (root / "output.md").write_text("# Output Contract\n\nFields.\n")
    for lane in STRIDE_CATEGORIES:
        lane_dir = root / "lanes" / lane
        lane_dir.mkdir(parents=True)
        (lane_dir / "exemplars.md").write_text(f"# {lane}\n\nExemplars.\n")
    return root


@pytest.fixture
def loader(prompts_root):
    return MarkdownLoader(prompts_root)


@pytest.fixture
def package_loader(package_root):
    return MarkdownLoader(package_root)


class TestSharedLoader:
    def test_prompts_load_through_the_same_loader(self, loader):
        assert set(PROMPT_BODY_NAMES) <= set(loader.names())

    def test_the_shared_root_carries_no_frameworks_exemplars(
        self, loader, package_loader
    ):
        """The split the two loaders exist for, stated as a property.

        ``analyze.md`` is one body serving every registered framework's lane
        agents; the worked drafts beneath it are written in one framework's
        record shape. A shared root holding them would make the second framework
        read the first's.
        """
        assert not any(name.startswith("lanes/") for name in loader.names())
        assert lane_exemplars_doc("spoofing") in package_loader.names()

    def test_missing_prompt_fails_closed(self, loader):
        with pytest.raises(MarkdownNotFoundError):
            loader.load("planner")

    def test_traversal_is_denied_as_absent(self, loader, tmp_path):
        (tmp_path.parent / "outside.md").write_text("secret")
        with pytest.raises(MarkdownNotFoundError):
            loader.load("../outside")


class TestComposeAnalyzePrompt:
    def test_body_precedes_the_contract_which_precedes_the_exemplars(
        self, loader, package_loader
    ):
        """Stable-first: shared body, then the package's, then the lane's.

        The order is the cacheable prefix, so it is a property rather than a
        preference — a lane's own text ahead of the contract every lane of that
        framework shares would cut the prefix at the first lane boundary.
        """
        composed = compose_analyze_prompt(loader, package_loader, "tampering")
        assert composed.index("## Role") < composed.index("Fields.")
        assert composed.index("Fields.") < composed.index("Exemplars.")

    def test_only_the_requested_lane_is_included(self, loader, package_loader):
        composed = compose_analyze_prompt(loader, package_loader, "tampering")
        assert "# tampering" in composed
        assert "# spoofing" not in composed

    def test_shared_body_is_identical_across_lanes(self, loader, package_loader):
        prefixes = {
            compose_analyze_prompt(loader, package_loader, lane).split("# ")[1]
            for lane in STRIDE_CATEGORIES
        }
        assert len(prefixes) == 1

    def test_state_placeholders_survive_composition(self, loader, package_loader):
        assert "{lane}" in compose_analyze_prompt(loader, package_loader, "spoofing")

    def test_missing_exemplar_file_fails_closed(
        self, loader, package_loader, package_root
    ):
        (package_root / "lanes" / "spoofing" / "exemplars.md").unlink()
        with pytest.raises(MarkdownNotFoundError):
            compose_analyze_prompt(loader, package_loader, "spoofing")


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


def test_exemplar_name_is_relative_to_the_package_root(package_loader):
    """The name is one lane's own directory, under whichever package owns it.

    Root-relative and framework-free: the loader is already rooted at
    ``frameworks/<name>/``, so a second package's exemplars answer to exactly
    this name through its own loader.
    """
    assert lane_exemplars_doc("denial-of-service") == (
        "lanes/denial-of-service/exemplars"
    )
    assert package_loader.load(lane_exemplars_doc("denial-of-service"))
