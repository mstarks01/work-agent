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

THE THREE GROUNDS LINTS exist because the exemplar system now ships a labelled
source block, and the reason it ships one is mechanical rather than editorial:
an exemplar quote citing text the prompt never shows would teach precisely the
failure finding-level attribution exists to prevent — that a plausible-sounding
quote can be produced from nothing. That is a property, so it is checked rather
than left to the author's care. The quote check runs the **shipped** ladder,
imported rather than reimplemented, so the exemplars are held to the identical
rule the agent is held to; a divergence between the two would be invisible
otherwise.

One hazard for whoever edits the source block: :func:`exemplar_system_ids`
takes its known-ID set from the *whole* ``## Input`` section, which now contains
free submitter prose. A backticked ``type:slug`` written into that prose would
silently widen the set. Submitter prose has no reason to spell an element ID —
the block reads the way a real ``source.md`` does, and those never do — but the
trap is worth knowing about.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from stride_service.grounding import verify_quote
from stride_service.markdown_loader import MarkdownLoader, split_sections
from stride_service.prompts import (
    ANALYZE_PROMPT_NAME,
    ANALYZE_PROMPT_TOKEN_CAP,
    CRITIC_PROMPT_NAME,
    CRITIC_PROMPT_TOKEN_CAP,
    EXEMPLAR_TOKEN_CAP,
    EXTRACT_PROMPT_NAME,
    EXTRACT_PROMPT_TOKEN_CAP,
    PROMPT_BODY_NAMES,
    PROMPT_SECTION_HEADINGS,
    RECRITIC_PROMPT_NAME,
    RECRITIC_PROMPT_TOKEN_CAP,
    REPAIR_PROMPT_NAME,
    REPAIR_PROMPT_TOKEN_CAP,
    compose_analyze_prompt,
    exemplar_name,
)
from stride_service.report import CATEGORY_LETTERS, STRIDE_CATEGORIES, DraftThreat
from stride_service.skills import estimate_tokens

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

loader = MarkdownLoader(PROMPTS_DIR)

BODY_TOKEN_CAPS = {
    ANALYZE_PROMPT_NAME: ANALYZE_PROMPT_TOKEN_CAP,
    CRITIC_PROMPT_NAME: CRITIC_PROMPT_TOKEN_CAP,
    RECRITIC_PROMPT_NAME: RECRITIC_PROMPT_TOKEN_CAP,
    EXTRACT_PROMPT_NAME: EXTRACT_PROMPT_TOKEN_CAP,
    REPAIR_PROMPT_NAME: REPAIR_PROMPT_TOKEN_CAP,
}

JSON_BLOCK_RE = re.compile(r"^```json\n(.*?)^```", re.MULTILINE | re.DOTALL)
# The exemplar system's rendered source: a fence, the `label:` line, the `----`
# rule, then the text — the shape ``render_sources`` produces for a real job.
SOURCE_BLOCK_RE = re.compile(
    r"^(?P<fence>`{3,})\nlabel: (?P<label>.+)\n----\n(?P<text>.*?)^(?P=fence)$",
    re.MULTILINE | re.DOTALL,
)
# Element and flow IDs as the prompts write them: `type:normalized-name`.
ELEMENT_ID_RE = re.compile(r"`((?:entity|process|store|flow|boundary):[a-z0-9:-]+)`")


def json_blocks(text):
    return JSON_BLOCK_RE.findall(text)


def exemplar_sections(category):
    return split_sections(loader.load(exemplar_name(category)))


def exemplar_system_ids():
    """Every element/flow ID the shared exemplar system defines."""
    input_section = split_sections(loader.load(ANALYZE_PROMPT_NAME))["Input"]
    return set(ELEMENT_ID_RE.findall(input_section))


def exemplar_source_block():
    """The exemplar system's one source, as ``(label, text)``.

    Found by shape rather than by position: a fenced block whose first line is
    ``label:`` is exactly what :func:`~stride_service.sources.render_sources`
    emits, which is the shape the block exists to depict.
    """
    input_section = split_sections(loader.load(ANALYZE_PROMPT_NAME))["Input"]
    match = SOURCE_BLOCK_RE.search(input_section)
    assert match, "the exemplar system carries no labelled source block"
    return match.group("label").strip(), match.group("text")


def exemplar_drafts(category):
    """Every draft in one exemplar file, parsed."""
    return [
        DraftThreat.model_validate(json.loads(block))
        for body in exemplar_sections(category).values()
        for block in json_blocks(body)
    ]


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
def test_exemplar_quote_grounds_verify_against_block(category):
    """Every exemplar quote is really in the block the prompt shows.

    Through the shipped ladder, imported — the exemplars are held to the exact
    rule ``join_drafts`` holds the agent to, so the two cannot drift apart
    unnoticed. An exemplar quoting text that appears nowhere would teach the
    one thing this whole feature exists to prevent.
    """
    _, text = exemplar_source_block()
    unfindable = [
        (draft.id, ground.text)
        for draft in exemplar_drafts(category)
        for ground in draft.grounds
        if ground.kind == "quote" and not verify_quote(ground.text, text)
    ]
    assert not unfindable


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_quote_labels_match_the_block(category):
    """A quote resolves to a source, which here is the block's declared label."""
    label, _ = exemplar_source_block()
    mislabelled = [
        (draft.id, ground.source_label)
        for draft in exemplar_drafts(category)
        for ground in draft.grounds
        if ground.kind == "quote" and ground.source_label != label
    ]
    assert not mislabelled


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_ground_refs_resolve(category):
    """A ground's element and flow references exist in the exemplar system.

    The reference lint beside this one covers ``affected_element_ids`` only, so
    without this all three grounds reference surfaces would be unchecked.
    """
    known_ids = exemplar_system_ids()
    dangling = [
        (draft.id, ref)
        for draft in exemplar_drafts(category)
        for ground in draft.grounds
        for ref in (ground.element_id, ground.flow_id)
        if ref and ref not in known_ids
    ]
    assert not dangling


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


# Worst-case category-agent instruction is skill text (~2.2K) plus this; the
# envelope is 6-8K, so the composed prompt has ~4K of room.
#
# Raised from 3500 with the body cap, and it had to move with it: the composed
# budget binds first, so a body cap the composed budget cannot accommodate is a
# cap nothing can reach.
COMPOSED_ANALYZE_TOKEN_BUDGET = 3800


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_composed_analyze_prompt_within_budget(category):
    composed = compose_analyze_prompt(loader, category)
    assert estimate_tokens(composed) <= COMPOSED_ANALYZE_TOKEN_BUDGET
