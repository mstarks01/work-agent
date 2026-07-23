"""CI lints over the real ``prompts/**/*.md`` tree.

The counterpart of ``test_skill_lints.py`` for prompt content (tickets 019,
020): the four agent prompts carry exactly the four fixed H2 headings in
order, the six exemplar files match the ``StrideCategory`` literals, and
every fenced ``json`` block in them parses as a ``DraftThreat`` citing only
elements the exemplar system defines. An exemplar that cites an element that
does not exist teaches the model to hallucinate IDs, so that check is worth
as much as the shape ones.

``## Output`` sections are prose-only by design — the machine-enforced shape
lives in ``DraftThreat`` and nowhere else — so a fenced block appearing there
is the drift this guards against.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from stride_service.markdown_loader import MarkdownLoader, split_sections
from stride_service.prompts import (
    ANALYST_PROMPT_NAME,
    ANALYST_PROMPT_TOKEN_CAP,
    CRITIC_PROMPT_NAME,
    CRITIC_PROMPT_TOKEN_CAP,
    EXEMPLAR_TOKEN_CAP,
    EXEMPLARS_PREFIX,
    EXTRACT_PROMPT_NAME,
    EXTRACT_PROMPT_TOKEN_CAP,
    PROMPT_BODY_NAMES,
    PROMPT_SECTION_HEADINGS,
    RECRITIC_PROMPT_NAME,
    RECRITIC_PROMPT_TOKEN_CAP,
    REPAIR_PROMPT_NAME,
    REPAIR_PROMPT_TOKEN_CAP,
    compose_analyst_prompt,
    exemplar_name,
)
from stride_service.report import CATEGORY_LETTERS, STRIDE_CATEGORIES, DraftThreat
from stride_service.skills import estimate_tokens

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

loader = MarkdownLoader(PROMPTS_DIR)

BODY_TOKEN_CAPS = {
    ANALYST_PROMPT_NAME: ANALYST_PROMPT_TOKEN_CAP,
    CRITIC_PROMPT_NAME: CRITIC_PROMPT_TOKEN_CAP,
    RECRITIC_PROMPT_NAME: RECRITIC_PROMPT_TOKEN_CAP,
    EXTRACT_PROMPT_NAME: EXTRACT_PROMPT_TOKEN_CAP,
    REPAIR_PROMPT_NAME: REPAIR_PROMPT_TOKEN_CAP,
}

JSON_BLOCK_RE = re.compile(r"^```json\n(.*?)^```", re.MULTILINE | re.DOTALL)
# Element and flow IDs as the prompts write them: `type:normalized-name`.
ELEMENT_ID_RE = re.compile(
    r"`((?:entity|process|store|flow|boundary):[a-z0-9:-]+)`"
)


def json_blocks(text):
    return JSON_BLOCK_RE.findall(text)


def exemplar_sections(category):
    return split_sections(loader.load(exemplar_name(category)))


def exemplar_system_ids():
    """Every element/flow ID the shared exemplar system defines."""
    input_section = split_sections(loader.load(ANALYST_PROMPT_NAME))["Input"]
    return set(ELEMENT_ID_RE.findall(input_section))


@pytest.mark.parametrize("name", PROMPT_BODY_NAMES)
def test_prompt_body_has_exact_fixed_headings_in_order(name):
    sections = split_sections(loader.load(name))
    assert list(sections) == list(PROMPT_SECTION_HEADINGS)


@pytest.mark.parametrize("name", PROMPT_BODY_NAMES)
def test_prompt_body_sections_are_nonempty(name):
    sections = split_sections(loader.load(name))
    empty = [heading for heading, body in sections.items() if not body]
    assert not empty


@pytest.mark.parametrize("name", PROMPT_BODY_NAMES)
def test_prompt_output_section_carries_no_fenced_block(name):
    """The output shape lives in ``DraftThreat``; duplicating it here drifts."""
    assert "```" not in split_sections(loader.load(name))["Output"]


@pytest.mark.parametrize("name", PROMPT_BODY_NAMES)
def test_prompt_body_within_token_cap(name):
    assert estimate_tokens(loader.load(name)) <= BODY_TOKEN_CAPS[name]


def test_exemplar_files_match_stride_categories_exactly():
    stems = sorted(path.stem for path in (PROMPTS_DIR / "exemplars").glob("*.md"))
    assert stems == sorted(STRIDE_CATEGORIES)


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_file_has_at_least_three_sections(category):
    assert len(exemplar_sections(category)) >= 3


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_each_exemplar_section_holds_exactly_one_json_block(category):
    counts = {
        heading: len(json_blocks(body))
        for heading, body in exemplar_sections(category).items()
    }
    assert set(counts.values()) == {1}, counts


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_every_exemplar_block_parses_as_a_draft_threat(category):
    for heading, body in exemplar_sections(category).items():
        for block in json_blocks(body):
            try:
                draft = DraftThreat.model_validate(json.loads(block))
            except (ValidationError, json.JSONDecodeError) as exc:
                pytest.fail(f"{category} '## {heading}': {exc}")
            assert draft.category == category
            assert draft.id.startswith(f"{CATEGORY_LETTERS[category]}-")


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_references_resolve_in_the_exemplar_system(category):
    known_ids = exemplar_system_ids()
    for body in exemplar_sections(category).values():
        for block in json_blocks(body):
            draft = DraftThreat.model_validate(json.loads(block))
            unknown = set(draft.affected_element_ids) - known_ids
            assert not unknown, f"{draft.id} cites {sorted(unknown)}"


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_file_within_token_cap(category):
    tokens = estimate_tokens(loader.load(exemplar_name(category)))
    assert tokens <= EXEMPLAR_TOKEN_CAP


def test_no_stray_prompt_files():
    known = {*PROMPT_BODY_NAMES, *(exemplar_name(c) for c in STRIDE_CATEGORIES)}
    assert set(loader.names()) == known


def test_no_non_markdown_files_under_prompts():
    stray = [
        path.relative_to(PROMPTS_DIR).as_posix()
        for path in PROMPTS_DIR.rglob("*")
        if path.is_file() and path.suffix != ".md"
    ]
    assert not stray


# Worst-case analyst instruction is skill text (~2.2K) plus this; ticket 006's
# envelope is 6-8K, so the composed prompt has ~4K of room.
COMPOSED_ANALYST_TOKEN_BUDGET = 3500


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_composed_analyst_prompt_within_budget(category):
    composed = compose_analyst_prompt(loader, category)
    assert estimate_tokens(composed) <= COMPOSED_ANALYST_TOKEN_BUDGET
