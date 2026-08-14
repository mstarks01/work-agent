"""CI lints over the real ``prompts/**/*.md`` tree.

The counterpart of ``test_skill_lints.py`` for prompt content (tickets 019,
020): the four agent prompts carry exactly the four fixed H2 headings in
order, the six exemplar files match the ``StrideCategory`` literals, and
every fenced ``json`` block in them parses as a ``ThreatProposal`` citing only
elements an exemplar system defines. An exemplar that cites an element that
does not exist teaches the model to hallucinate IDs, so that check is worth
as much as the shape ones.

``## Output`` sections are prose-only by design — the machine-enforced shape
lives in ``ThreatProposal`` and nowhere else — so a fenced block appearing there
is the drift this guards against.

``analyze.md`` carries **two** worked reference systems
(``docs/adr/0006-two-exemplar-systems.md``), so every check that resolves an
exemplar's IDs, quotes or evidence refs first asks which system that draft is
written against — :func:`owning_system`, which fails a draft whose cited
elements no single system covers. Resolving against the union of the two would
accept a draft that mixed them, and mixing is the one thing the prompt tells an
agent never to do with them.

THE EVIDENCE LINTS exist because each exemplar system ships a labelled source
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

One hazard for whoever edits a source block: :func:`system_ids` takes its
known-ID set from the *whole* subsection, which contains free submitter prose.
A backticked ``type:slug`` written into that prose would silently widen the
set. Submitter prose has no reason to spell an element ID — the blocks read the
way a real ``source.md`` does, and those never do — but the trap is worth
knowing about.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from stride_service.critic import mentioned_ids
from stride_service.evidence import (
    ABSENT_PREFIX,
    CROSSING_PREFIX,
    UNKNOWN_PREFIX,
    render_catalog,
)
from stride_service.frameworks.stride.record import STRIDE_CATEGORIES, ThreatProposal
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
    lane_exemplars_doc,
)
from stride_service.report import Ground
from stride_service.skills import estimate_tokens

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
# The exemplars moved with the package that owns them: a worked draft is written
# in one framework's record shape, so it lives under that framework's root while
# the body that frames it stays shared (ADR 0011).
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "frameworks" / "stride"

loader = MarkdownLoader(PROMPTS_DIR)
package_loader = MarkdownLoader(PACKAGE_DIR)

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
# One exemplar system: everything under its H4 up to the next heading of the
# same level or higher.
EXEMPLAR_SYSTEM_RE = re.compile(
    r"^#### Exemplar system (?P<name>[A-Z]):[^\n]*\n(?P<body>.*?)(?=^#{1,4} )",
    re.MULTILINE | re.DOTALL,
)


def json_blocks(text):
    return JSON_BLOCK_RE.findall(text)


def exemplar_sections(lane):
    return split_sections(package_loader.load(lane_exemplars_doc(lane)))


def exemplar_systems():
    """The ``#### Exemplar system X`` subsections of ``analyze.md``, by letter.

    Found by their own headings rather than by which H2 contains them. The
    blocks are static reference material, so they sit wherever the prompt's
    static content sits, and a helper keyed on the enclosing heading would have
    to be rewritten every time that moves. Keyed on the headings that name
    them, this does not.
    """
    body = loader.load(ANALYZE_PROMPT_NAME)
    systems = {
        match.group("name"): match.group("body")
        for match in EXEMPLAR_SYSTEM_RE.finditer(body)
    }
    assert len(systems) >= 2, (
        "analyze.md carries fewer than two '#### Exemplar system X' subsections. "
        "One reference system is the exemplar-domain monoculture "
        "docs/adr/0006-two-exemplar-systems.md exists to prevent."
    )
    return systems


def system_ids(system):
    """Every element/flow ID one exemplar system defines."""
    return set(ELEMENT_ID_RE.findall(system))


def exemplar_system_ids():
    """Every element/flow ID any exemplar system defines."""
    return set().union(*(system_ids(body) for body in exemplar_systems().values()))


def source_block(system):
    """One exemplar system's source, as ``(label, text)``.

    Found by shape rather than by position: a fenced block whose first line is
    ``label:`` is exactly what :func:`~stride_service.sources.render_sources`
    emits, which is the shape the block exists to depict.
    """
    match = SOURCE_BLOCK_RE.search(system)
    assert match, "an exemplar system carries no labelled source block"
    return match.group("label").strip(), match.group("text")


def catalog(system):
    """One exemplar system's evidence catalog, as the list of IDs it renders to.

    Found by shape, like the source block: the rows of the table whose left
    column is a backticked ID, which is exactly what ``prepare_analysis`` puts
    in front of an agent (:func:`~stride_service.evidence.render_catalog`).
    A table rather than a JSON array because a list of well-formed IDs reads as
    a specimen of the format and got composed from rather than selected out of
    (#138); the exemplars show the shape an agent actually receives, so they
    moved with it.
    """
    refs = [
        ref
        for ref in re.findall(r"^\| `([^`]+)` \| ", system, re.MULTILINE)
        if ref.startswith(
            (f"{UNKNOWN_PREFIX}:", f"{ABSENT_PREFIX}:", f"{CROSSING_PREFIX}:")
        )
    ]
    if not refs:
        raise AssertionError("an exemplar system carries no evidence catalog block")
    return refs


def owning_system(proposal):
    """Which exemplar system a draft is written against, by its own IDs.

    The check that makes two systems safe rather than merely twice as much
    material. Every other exemplar lint resolves against *this* system alone,
    so a draft that reached across the two — citing payments elements while
    quoting the telemetry source — fails here rather than passing a union that
    would let it teach exactly the mixing the prompt forbids.
    """
    cited = set(proposal.affected_element_ids)
    owners = [
        name for name, body in exemplar_systems().items() if cited <= system_ids(body)
    ]
    assert len(owners) == 1, (
        f"{proposal.id} cites {sorted(cited)}, which no single exemplar system "
        f"covers (matched {owners}) — a draft argues about one system"
    )
    return exemplar_systems()[owners[0]]


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
    lanes = sorted(path.name for path in (PACKAGE_DIR / "lanes").iterdir())
    assert lanes == sorted(STRIDE_CATEGORIES)
    for lane in lanes:
        assert (PACKAGE_DIR / "lanes" / lane / "exemplars.md").is_file()


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
    would reject is a worked example of a dead run.

    ``extra="forbid"`` is doing real work here: an exemplar spelling out an
    ``id`` or a ``category`` would be teaching an agent to emit two fields the
    lane already determines, and it fails this parse rather than being read as
    harmless decoration."""
    for heading, body in exemplar_sections(category).items():
        for block in json_blocks(body):
            try:
                proposal = ThreatProposal.model_validate(json.loads(block))
            except (ValidationError, json.JSONDecodeError) as exc:
                pytest.fail(f"{category} '## {heading}': {exc}")
            assert proposal.sequence >= 1


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_references_resolve_in_the_exemplar_system(category):
    """Every cited element exists, and all of them in the same worked system."""
    for proposal in exemplar_proposals(category):
        known_ids = system_ids(owning_system(proposal))
        unknown = set(proposal.affected_element_ids) - known_ids
        assert not unknown, f"{proposal.id} cites {sorted(unknown)}"


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_descriptions_cite_only_ids_the_exemplar_system_has(category):
    """The prose half, through the extractor the service marks reports with.

    An exemplar naming an element its own worked system does not contain would
    be teaching the very thing ``UnresolvedMention`` exists to catch, in the
    six prompts that demonstrate what a good description looks like.
    """
    for proposal in exemplar_proposals(category):
        known_ids = system_ids(owning_system(proposal))
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
    """The numbering rule the prompt states, demonstrated by its own drafts.

    Asserted on ``sequence``, which is what an agent now supplies and therefore
    what it can get wrong. ``numbering_gaps`` asks the same question one seam
    later, of the composed IDs the service builds out of these numbers.
    """
    sequences = sorted(p.sequence for p in exemplar_proposals(category))
    assert sequences == list(range(1, len(sequences) + 1))


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_quotes_verify_against_block(category):
    """Every exemplar quote is really in the block the prompt shows.

    Through the shipped ladder, imported — the exemplars are held to the exact
    rule ``join_drafts`` holds the agent to, so the two cannot drift apart
    unnoticed. An exemplar quoting text that appears nowhere would teach the
    one thing this whole feature exists to prevent.
    """
    unfindable = [
        (proposal.id, quote.text)
        for proposal in exemplar_proposals(category)
        for quote in proposal.quotes
        if not verify_quote(quote.text, source_block(owning_system(proposal))[1])
    ]
    assert not unfindable


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_quote_labels_match_the_block(category):
    """A quote resolves to a source, which here is the block's declared label.

    With two worked systems the label is load-bearing rather than decorative:
    it is what picks the block the quote above is verified against, exactly as
    a real draft's ``source_label`` picks one of the job's sources.
    """
    mislabelled = [
        (proposal.id, quote.source_label)
        for proposal in exemplar_proposals(category)
        for quote in proposal.quotes
        if quote.source_label != source_block(owning_system(proposal))[0]
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
    dangling = [
        (proposal.id, ref)
        for proposal in exemplar_proposals(category)
        for ref in proposal.evidence_refs
        if ref not in set(catalog(owning_system(proposal)))
    ]
    assert not dangling


@pytest.mark.parametrize("name", sorted(exemplar_systems()))
def test_the_exemplar_catalog_names_only_elements_the_system_defines(name):
    """The other end of the same chain: the catalog's own IDs resolve.

    A catalog is derived from a System Model at run time, so every entry names
    a real element by construction. An exemplar system's is hand-written, and
    an entry naming nothing would make the lint above pass against a fiction.
    """
    system = exemplar_systems()[name]
    known_ids = system_ids(system)
    dangling = []
    for ref in catalog(system):
        if ref.startswith(f"{CROSSING_PREFIX}:"):
            element = ref.split(":", 1)[1]
        elif ref.startswith((f"{UNKNOWN_PREFIX}:", f"{ABSENT_PREFIX}:")):
            element = ref.split(":", 1)[1].rsplit(":", 1)[0]
        else:
            dangling.append(ref)
            continue
        if element not in known_ids:
            dangling.append(ref)
    assert not dangling


@pytest.mark.parametrize("name", sorted(exemplar_systems()))
def test_the_exemplar_catalog_is_rendered_the_way_a_real_one_is(name):
    """The exemplar table is what ``render_catalog`` would emit, byte for byte.

    The two lints above pin the *IDs*; this pins the shape around them — the
    count line, the header row, and the gloss in the right column. That shape
    is the #138 fix rather than decoration, so an exemplar drifting from it
    would teach agents to read a table they will not be given, which is the
    failure the fix exists to prevent. Without this the drift is silent in the
    direction that matters: :func:`~stride_service.evidence._gloss` is free to
    change and nothing here would notice.

    Rebuilding the grounds from the refs is exact rather than approximate — a
    ref is *built* from a ground, and the three branches are separated by their
    prefixes — so these are the objects a real catalog would hold. The two
    attribute branches are what makes the prefix load-bearing here: they carry
    identical fields and different glosses, so a row written under the wrong
    prefix renders "never stated" where the system states an absence, and this
    is the check that says so.
    """
    system = exemplar_systems()[name]
    entries = {}
    for ref in catalog(system):
        if ref.startswith(f"{CROSSING_PREFIX}:"):
            entries[ref] = Ground(kind="derived-fact", flow_id=ref.split(":", 1)[1])
        else:
            kind = (
                "absent-attribute"
                if ref.startswith(f"{ABSENT_PREFIX}:")
                else "unknown-attribute"
            )
            element, attribute = ref.split(":", 1)[1].rsplit(":", 1)
            entries[ref] = Ground(kind=kind, element_id=element, attribute=attribute)
    assert render_catalog(entries).strip() in system


def test_every_exemplar_system_is_worked_by_some_category():
    """A second system nobody demonstrates is cost without diversity.

    The whole point of carrying two is that agents see the method applied to
    both, so a system that ships in the prompt and appears in no draft is
    ~500 tokens on every job buying nothing.
    """
    worked = {
        owning_system(proposal)
        for category in STRIDE_CATEGORIES
        for proposal in exemplar_proposals(category)
    }
    unworked = [name for name, body in exemplar_systems().items() if body not in worked]
    assert not unworked, f"exemplar systems {unworked} are shown but never worked"


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_exemplar_file_within_token_cap(category):
    tokens = estimate_tokens(package_loader.load(lane_exemplars_doc(category)))
    assert tokens <= EXEMPLAR_TOKEN_CAP


def test_no_stray_prompt_files():
    """The shared root is the five bodies and nothing else.

    Tighter than before the cutover, not looser: the exemplars used to sit here
    too, and every one of them was STRIDE's. What is left is the text that
    genuinely serves every registered framework, which is what makes the shared
    root shared rather than merely first.
    """
    assert set(loader.names()) == set(PROMPT_BODY_NAMES)


def test_no_non_markdown_files_under_prompts():
    stray = [
        path.relative_to(PROMPTS_DIR).as_posix()
        for path in PROMPTS_DIR.rglob("*")
        if path.is_file() and path.suffix != ".md"
    ]
    assert not stray


# Worst-case category-agent instruction is skill text (~2.2K) plus this, against
# a 6-8K envelope. At this number the worst case sits around 7K, which is inside
# the envelope and no longer comfortably so — the second exemplar system spent
# most of the room that used to be here. The next thing that wants space in a
# category agent's *static* instruction should be weighed against deleting
# something, not against this line.
#
# Moves with the body cap, and has to: the composed budget binds first, so a
# body cap the composed budget cannot accommodate is a cap nothing can reach.
#
# What the envelope now also carries, and what this number does not: the
# job-varying block grew by up to two domain packs (~1.4K at the
# ``DOMAIN_PACK_TOKEN_CAP``) plus one lane's candidates, the evidence catalog,
# and — for a lane whose rules fired — up to two retrieved notes and one case
# (~1.1K at their own caps). Those are runtime values rather than prompt text,
# capped where they are produced; this budget governs the static instruction
# only, and it moves with the body cap it has to accommodate — by 100 for the
# retrieved corpus, then by 300 for the evidence catalog becoming a table
# (#138), and now by 200 for that catalog learning to say "stated absent"
# (#171), each argued at ``ANALYZE_PROMPT_TOKEN_CAP`` rather than restated
# here.
#
# The worst lane sits at ~5.4K of the 5.5K, and the worst case is now ~7.2K
# against the 6-8K envelope. The paragraph above already said the next thing
# wanting static room should be weighed against deleting something; #171 was
# weighed that way and one deletion came with it — the canonical tampering
# draft dropped the quote it used as a workaround for the rows this ticket
# adds. That is the shape the next raise needs, and there is no longer room
# for one that does not have it.
#
# The catalog's *runtime* rendering grew too, by roughly one short clause per
# entry. It is job-varying so it does not land in this number, but it is not
# free: a large model pays it per lane, which is the trade for jobs that no
# longer die on a composed reference.
COMPOSED_ANALYZE_TOKEN_BUDGET = 5500


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_composed_analyze_prompt_within_budget(category):
    composed = compose_analyze_prompt(loader, package_loader, category)
    assert estimate_tokens(composed) <= COMPOSED_ANALYZE_TOKEN_BUDGET
