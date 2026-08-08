"""CI lints over the real ``prompts/**/*.md`` tree.

The counterpart of ``test_skill_lints.py`` for prompt content (tickets 019,
020): the four agent prompts carry exactly the four fixed H2 headings in
order, the six exemplar files match the ``StrideCategory`` literals, and
every fenced ``json`` block in them parses as a ``ThreatProposal`` citing only
elements the exemplar system defines. An exemplar that cites an element that
does not exist teaches the model to hallucinate IDs, so that check is worth
as much as the shape ones.

``## Output`` sections are prose-only by design — the machine-enforced shape
lives in ``ThreatProposal`` and nowhere else — so a fenced block appearing there
is the drift this guards against.

THE EVIDENCE LINTS exist because the exemplar system ships a labelled source
block and an evidence catalog, and both ship for mechanical rather than
editorial reasons. An exemplar quote citing text the prompt never shows would
teach precisely the failure finding-level attribution exists to prevent — that
a plausible-sounding quote can be produced from nothing. An exemplar citing an
evidence ID its own worked system's catalog does not list would teach the other
half: that an ID can be composed rather than copied, which is the one thing an
agent must never do with one. Both are properties, so both are checked rather
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

from stride_service.critic import mentioned_ids, numbering_gaps
from stride_service.evidence import CROSSING_PREFIX, UNKNOWN_PREFIX
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
from stride_service.report import CATEGORY_LETTERS, STRIDE_CATEGORIES, ThreatProposal
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
# A job-varying placeholder ADK templates from session state at run time. The
# first one in an instruction is where its cacheable prefix stops.
PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}")
# The shared exemplar system: everything under its H3 up to the next heading of
# the same level or higher.
EXEMPLAR_SYSTEM_RE = re.compile(
    r"^### The exemplar system\n(?P<body>.*?)(?=^#{1,3} )", re.MULTILINE | re.DOTALL
)


def json_blocks(text):
    return JSON_BLOCK_RE.findall(text)


def exemplar_sections(category):
    return split_sections(loader.load(exemplar_name(category)))


def exemplar_system():
    """The ``### The exemplar system`` subsection of ``analyze.md``.

    Found by its own heading rather than by which H2 contains it. The block is
    static reference material, so it sits wherever the prompt's static content
    sits — it moved out of ``## Input`` when the static/variable split was
    tightened for caching, and a helper keyed on the enclosing H2 had to be
    rewritten to follow it. Keyed on the heading that names it, it does not.
    """
    body = loader.load(ANALYZE_PROMPT_NAME)
    match = EXEMPLAR_SYSTEM_RE.search(body)
    assert match, "analyze.md carries no '### The exemplar system' subsection"
    return match.group("body")


def exemplar_system_ids():
    """Every element/flow ID the shared exemplar system defines."""
    return set(ELEMENT_ID_RE.findall(exemplar_system()))


def exemplar_source_block():
    """The exemplar system's one source, as ``(label, text)``.

    Found by shape rather than by position: a fenced block whose first line is
    ``label:`` is exactly what :func:`~stride_service.sources.render_sources`
    emits, which is the shape the block exists to depict.
    """
    match = SOURCE_BLOCK_RE.search(exemplar_system())
    assert match, "the exemplar system carries no labelled source block"
    return match.group("label").strip(), match.group("text")


def exemplar_catalog():
    """The exemplar system's evidence catalog, as the list of IDs it renders to.

    Found by shape, like the source block: the one fenced block in the section
    that parses as a JSON array of strings, which is exactly what
    ``prepare_analysis`` puts in front of an agent.
    """
    for block in re.findall(
        r"^`{3,}\n(.*?)^`{3,}$", exemplar_system(), re.MULTILINE | re.DOTALL
    ):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list) and all(isinstance(ref, str) for ref in parsed):
            return parsed
    raise AssertionError("the exemplar system carries no evidence catalog block")


def exemplar_proposals(category):
    """Every proposal in one exemplar file, parsed."""
    return [
        ThreatProposal.model_validate(json.loads(block))
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
def test_nothing_static_follows_the_last_placeholder(name):
    """Static prose after the last placeholder is prose that can never cache.

    A prompt's cacheable prefix ends at its first job-varying byte, so every
    static word written *after* one is paid for on every call forever. The
    framing a section needs — what the blocks are, that they are data and not
    instruction, what standing each carries — says the same thing whether it
    precedes the placeholder or follows it, and only one of those positions is
    free.

    The rule is mechanical because the pull is real: the natural way to write
    an input section is to show the input and then explain it, which is exactly
    the layout that costs the most. Closing fences and whitespace are all that
    may trail, since a fence opened before a placeholder has to shut after it.
    """
    section = split_sections(loader.load(name))["Input"]
    last = None
    for match in PLACEHOLDER_RE.finditer(section):
        last = match
    assert last, f"{name}'s Input section carries no placeholder at all"
    trailing = [line.strip() for line in section[last.end() :].splitlines()]
    stray = [line for line in trailing if line and set(line) != {"`"}]
    assert not stray, (
        f"{name} carries static prose after its last placeholder, which can"
        f" never cache — move it above the placeholder it frames: {stray}"
    )


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
def test_every_exemplar_block_parses_as_a_threat_proposal(category):
    """The exemplars are parsed against the schema the agent emits, not the one
    the service resolves it into — an exemplar the node's own output schema
    would reject is a worked example of a dead run."""
    for heading, body in exemplar_sections(category).items():
        for block in json_blocks(body):
            try:
                proposal = ThreatProposal.model_validate(json.loads(block))
            except (ValidationError, json.JSONDecodeError) as exc:
                pytest.fail(f"{category} '## {heading}': {exc}")
            assert proposal.category == category
            assert proposal.id.startswith(f"{CATEGORY_LETTERS[category]}-")


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_references_resolve_in_the_exemplar_system(category):
    known_ids = exemplar_system_ids()
    for body in exemplar_sections(category).values():
        for block in json_blocks(body):
            proposal = ThreatProposal.model_validate(json.loads(block))
            unknown = set(proposal.affected_element_ids) - known_ids
            assert not unknown, f"{proposal.id} cites {sorted(unknown)}"


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_descriptions_cite_only_ids_the_exemplar_system_has(category):
    """The prose half, through the extractor the service marks reports with.

    An exemplar naming an element its own worked system does not contain would
    be teaching the very thing ``UnresolvedMention`` exists to catch, in the
    six prompts that demonstrate what a good description looks like.
    """
    known_ids = exemplar_system_ids()
    for body in exemplar_sections(category).values():
        for block in json_blocks(body):
            proposal = ThreatProposal.model_validate(json.loads(block))
            unknown = [
                mention
                for mention in mentioned_ids(proposal.description)
                if mention not in known_ids
            ]
            assert not unknown, f"{proposal.id} description cites {sorted(unknown)}"


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_drafts_carry_a_mitigation_or_the_unknown_that_excuses_one(category):
    """An exemplar must not model the shape the service marks as incomplete."""
    for proposal in exemplar_proposals(category):
        licensed = any(
            ref.startswith(f"{UNKNOWN_PREFIX}:") for ref in proposal.evidence_refs
        )
        assert proposal.mitigations or licensed, (
            f"{proposal.id} offers no mitigation and no unknown-attribute evidence"
        )


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_drafts_are_numbered_from_01_without_gaps(category):
    """The numbering rule the prompt states, demonstrated by the prompt's own drafts."""
    assert numbering_gaps(exemplar_proposals(category)) == []


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_quotes_verify_against_block(category):
    """Every exemplar quote is really in the block the prompt shows.

    Through the shipped ladder, imported — the exemplars are held to the exact
    rule ``join_drafts`` holds the agent to, so the two cannot drift apart
    unnoticed. An exemplar quoting text that appears nowhere would teach the
    one thing this whole feature exists to prevent.
    """
    _, text = exemplar_source_block()
    unfindable = [
        (proposal.id, quote.text)
        for proposal in exemplar_proposals(category)
        for quote in proposal.quotes
        if not verify_quote(quote.text, text)
    ]
    assert not unfindable


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_quote_labels_match_the_block(category):
    """A quote resolves to a source, which here is the block's declared label."""
    label, _ = exemplar_source_block()
    mislabelled = [
        (proposal.id, quote.source_label)
        for proposal in exemplar_proposals(category)
        for quote in proposal.quotes
        if quote.source_label != label
    ]
    assert not mislabelled


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_evidence_refs_are_in_the_exemplar_catalog(category):
    """Every ID an exemplar cites is one its own worked system offers.

    Membership, exactly as the resolver asks it. The reference lint beside this
    one covers ``affected_element_ids`` only, so without this the surface an
    agent copies from would be unchecked — and an exemplar composing an ID that
    resolves against nothing teaches an agent to do the same, at the one seam
    where that fails the whole lane.
    """
    catalog = set(exemplar_catalog())
    dangling = [
        (proposal.id, ref)
        for proposal in exemplar_proposals(category)
        for ref in proposal.evidence_refs
        if ref not in catalog
    ]
    assert not dangling


def test_the_exemplar_catalog_names_only_elements_the_system_defines():
    """The other end of the same chain: the catalog's own IDs resolve.

    A catalog is derived from a System Model at run time, so every entry names
    a real element by construction. The exemplar system's is hand-written, and
    an entry naming nothing would make the lint above pass against a fiction.
    """
    known_ids = exemplar_system_ids()
    dangling = []
    for ref in exemplar_catalog():
        if ref.startswith(f"{CROSSING_PREFIX}:"):
            element = ref.split(":", 1)[1]
        elif ref.startswith(f"{UNKNOWN_PREFIX}:"):
            element = ref.split(":", 1)[1].rsplit(":", 1)[0]
        else:
            dangling.append(ref)
            continue
        if element not in known_ids:
            dangling.append(ref)
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
# Moves with the body cap, and has to: the composed budget binds first, so a
# body cap the composed budget cannot accommodate is a cap nothing can reach.
COMPOSED_ANALYZE_TOKEN_BUDGET = 3900


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_composed_analyze_prompt_within_budget(category):
    composed = compose_analyze_prompt(loader, category)
    assert estimate_tokens(composed) <= COMPOSED_ANALYZE_TOKEN_BUDGET
