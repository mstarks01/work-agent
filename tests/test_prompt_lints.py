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
from typing import get_args

import pytest
from pydantic import ValidationError

from stride_service.actions import menu
from stride_service.critic import mentioned_ids
from stride_service.evidence import (
    ABSENT_PREFIX,
    CROSSING_PREFIX,
    UNKNOWN_PREFIX,
    render_catalog,
)
from stride_service.frameworks import PACKAGES, schemas_for
from stride_service.frameworks.stride.record import STRIDE_CATEGORIES
from stride_service.grounding import verify_quote
from stride_service.markdown_loader import MarkdownLoader, split_sections
from stride_service.prompts import (
    ANALYZE_PROMPT_NAME,
    PROMPT_BODY_NAMES,
    PROMPT_SECTION_HEADINGS,
    compose_analyze_prompt,
    lane_exemplars_doc,
)
from stride_service.report import Ground
from stride_service.skills import estimate_tokens
from stride_service.token_caps import (
    COMPOSED_ANALYZE_CAP,
    TOKEN_CAPS,
    prompt_key,
)

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
FRAMEWORKS_DIR = Path(__file__).resolve().parents[1] / "frameworks"
# The exemplars moved with the package that owns them: a worked draft is written
# in one framework's record shape, so it lives under that framework's root while
# the body that frames it stays shared (ADR 0011).
PACKAGE_DIR = FRAMEWORKS_DIR / "stride"

loader = MarkdownLoader(PROMPTS_DIR)
package_loader = MarkdownLoader(PACKAGE_DIR)
#: One loader per registered package, so an exemplar lint reads whichever
#: package's text it was parametrized for rather than the one that arrived
#: first. Built from ``PACKAGES``, so a framework added to the registry is a
#: framework these lints start running over with no edit here.
PACKAGE_LOADERS = {name: MarkdownLoader(FRAMEWORKS_DIR / name) for name in PACKAGES}

#: Every ``(framework, lane)`` pair whose exemplar file ships. The parametrize
#: argument for every lint below that reads a worked draft.
#:
#: **This is what #280 was.** These lints ran over ``STRIDE_CATEGORIES`` against
#: one package's loader, so ASVS's 17 exemplar files — the text 17 lane agents
#: learn their record's shape from — were checked by none of them. A lint that
#: names a package is the shape ``docs/agents/framework-parity.md`` exists to
#: catch, and ``tests/test_framework_neutrality.py`` cannot catch this one
#: because it puts test files out of scope on purpose.
EXEMPLAR_LANES = [
    (name, lane) for name, package in PACKAGES.items() for lane in package.lanes
]


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


def exemplar_sections(framework, lane):
    """One package's worked drafts for one lane, split into its H2 sections."""
    return split_sections(PACKAGE_LOADERS[framework].load(lane_exemplars_doc(lane)))


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

    **A draft that cites nothing has no owner, and that is a legal shape.** A
    framework ruling on whether a requirement applies files a claim saying it
    does not, and there is no element for such a claim to point at — ASVS ships
    ``V5.2.1 — This system accepts no uploaded file`` with an empty list. The
    empty set is a subset of both systems, so the assertion below would read
    "ambiguous" where the truth is "nothing was cited". Callers get ``None`` and
    skip the resolution checks, which have nothing to resolve.
    """
    cited = set(proposal.affected_element_ids)
    if not cited:
        return None
    owners = [
        name for name, body in exemplar_systems().items() if cited <= system_ids(body)
    ]
    assert len(owners) == 1, (
        f"cites {sorted(cited)}, which no single exemplar system "
        f"covers (matched {owners}) — a draft argues about one system"
    )
    return exemplar_systems()[owners[0]]


def system_for_label(label):
    """The exemplar system whose source block declares ``label``, or ``None``.

    A quote names its source by label, exactly as a real draft's
    ``source_label`` picks one of the job's sources. Resolving that way rather
    than through the draft's cited elements is what lets these lints check a
    draft that cites nothing — a ruling that a requirement does not apply has
    no element to point at and still rests on the submitter's own words.
    """
    for body in exemplar_systems().values():
        if source_block(body)[0] == label:
            return body
    return None


def proposal_type(framework):
    """The record one package's lane agent actually emits a claim as.

    A table lookup through ``schemas_for`` rather than a name, so an exemplar is
    parsed against its own framework's shape: STRIDE's carries ``sequence`` and
    ``severity``, ASVS's carries ``requirement``, and parsing either against the
    other's record is how a worked example of a dead run ships.
    """
    claims = schemas_for(framework).proposals.model_fields["claims"]
    return get_args(claims.annotation)[0]


def exemplar_proposals(framework, lane):
    """Every proposal in one package's exemplar file, parsed as its own record."""
    record = proposal_type(framework)
    return [
        record.model_validate(json.loads(block))
        for body in exemplar_sections(framework, lane).values()
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
    """The alarm: this body has not grown past what its cap allows.

    Failing here is not a verdict on the edit. Call ``alarm_at`` over the new
    size, write the answer into ``TOKEN_CAPS``, and say what the text buys.
    """
    assert estimate_tokens(loader.load(name)) <= TOKEN_CAPS[prompt_key(name)]


@pytest.mark.parametrize("name", PROMPT_BODY_NAMES)
def test_prompt_body_cap_still_alarms(name):
    """The other half of a drift alarm: a cap far above its file catches nothing.

    A cap more than twice its body has stopped measuring anything. Shrinking a
    body therefore costs one line here, which is the price of the alarm staying
    proportional to what it watches.
    """
    tokens = estimate_tokens(loader.load(name))
    assert TOKEN_CAPS[prompt_key(name)] <= 2 * tokens


def test_every_prompt_body_has_a_cap():
    """The table answers the registry, with nothing left over.

    A body with no key would raise on lookup, which the parametrized alarms
    already catch. The direction this adds is the quiet one: a key for a body
    that no longer exists, which reads as coverage and checks nothing.
    """
    keyed = {key.split("/", 1)[1] for key in TOKEN_CAPS if key.startswith("prompts/")}
    assert keyed == set(PROMPT_BODY_NAMES)


@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_exemplar_files_match_the_packages_declared_lanes(framework):
    """The tree and the declaration are one list, for every package."""
    root = FRAMEWORKS_DIR / framework / "lanes"
    lanes = sorted(path.name for path in root.iterdir())

    assert lanes == sorted(PACKAGES[framework].lanes)
    for lane in lanes:
        assert (root / lane / "exemplars.md").is_file()


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_exemplar_file_works_at_least_two_drafts(framework, lane):
    """A file with one draft teaches a case; two teach a distinction.

    Two rather than three, because three is STRIDE's choice rather than a rule
    about exemplars: its files work three or more, and ASVS's work two. What no
    framework can do is ship one, since every one of these files exists to show
    where a judgement goes one way and where it goes the other.
    """
    assert len(exemplar_sections(framework, lane)) >= 2


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_each_exemplar_section_holds_exactly_one_json_block(framework, lane):
    counts = {
        heading: len(json_blocks(body))
        for heading, body in exemplar_sections(framework, lane).items()
    }
    assert set(counts.values()) == {1}, counts


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_every_exemplar_block_parses_as_its_packages_proposal(framework, lane):
    """The exemplars are parsed against the schema the agent emits, not the one
    the service resolves it into — an exemplar the node's own output schema
    would reject is a worked example of a dead run.

    **Against its own package's record**, resolved through ``schemas_for``. A
    worked draft is written in one framework's record shape (ADR 0011), so
    parsing ASVS's against STRIDE's would fail on every file and parsing it
    against nothing is what shipped until #280.

    ``extra="forbid"`` is doing real work here: an exemplar spelling out an
    ``id`` or a ``category`` would be teaching an agent to emit two fields the
    lane already determines, and it fails this parse rather than being read as
    harmless decoration."""
    record = proposal_type(framework)
    for heading, body in exemplar_sections(framework, lane).items():
        for block in json_blocks(body):
            try:
                record.model_validate(json.loads(block))
            except (ValidationError, json.JSONDecodeError) as exc:
                pytest.fail(f"{framework} {lane} '## {heading}': {exc}")


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_exemplar_drafts_are_numbered_from_01_without_gaps(framework, lane):
    """The numbering rule the prompt states, demonstrated by its own drafts.

    Asserted on ``sequence``, which is what an agent supplies and therefore what
    it can get wrong. ``numbering_gaps`` asks the same question one seam later,
    of the composed IDs the service builds out of these numbers.

    **Keyed to the field, not to a framework name.** A package whose claims
    compose an identity from an action and a place numbers its drafts, and one
    whose claims name a catalog requirement heads them with that identifier
    instead — so the rule runs exactly where the record carries ``sequence``,
    and a package that does not carry it is skipped rather than exempted by
    name.
    """
    if "sequence" not in proposal_type(framework).model_fields:
        pytest.skip(f"{framework} claims carry no sequence to number")
    sequences = sorted(p.sequence for p in exemplar_proposals(framework, lane))
    assert sequences == list(range(1, len(sequences) + 1))


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_exemplar_references_resolve_in_the_exemplar_system(framework, lane):
    """Every cited element exists, and all of them in the same worked system."""
    for proposal in exemplar_proposals(framework, lane):
        system = owning_system(proposal)
        if system is None:
            continue
        unknown = set(proposal.affected_element_ids) - system_ids(system)
        assert not unknown, f"{framework} {lane} cites {sorted(unknown)}"


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_exemplar_descriptions_cite_only_ids_the_exemplar_system_has(framework, lane):
    """The prose half, through the extractor the service marks reports with.

    An exemplar naming an element its own worked system does not contain would
    be teaching the very thing ``UnresolvedMention`` exists to catch, in the
    files that demonstrate what a good description looks like.
    """
    for proposal in exemplar_proposals(framework, lane):
        system = owning_system(proposal)
        if system is None:
            continue
        known_ids = system_ids(system)
        unknown = [
            mention
            for mention in mentioned_ids(proposal.description)
            if mention not in known_ids
        ]
        assert not unknown, f"{framework} {lane} description cites {sorted(unknown)}"


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_exemplar_drafts_carry_a_mitigation_or_the_unknown_that_excuses_one(
    framework, lane
):
    """An exemplar must not model the shape the service marks as incomplete.

    **Keyed to the field, like the numbering rule.** ``MissingMitigation`` marks
    a claim that recommends nothing and rests on no unknown, which is a judgement
    only a record carrying ``mitigations`` can fail. A framework whose claims rule
    on whether a requirement applies recommends nothing by construction, so it is
    skipped for carrying no such field rather than exempted by name.
    """
    if "mitigations" not in proposal_type(framework).model_fields:
        pytest.skip(f"{framework} claims carry no mitigations to require")
    for proposal in exemplar_proposals(framework, lane):
        licensed = any(
            ref.startswith(f"{UNKNOWN_PREFIX}:") for ref in proposal.evidence_refs
        )
        assert proposal.mitigations or licensed, (
            f"{framework} {lane} offers no mitigation and no unknown-attribute evidence"
        )


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_exemplar_quotes_verify_against_block(framework, lane):
    """Every exemplar quote is really in the block the prompt shows.

    Through the shipped ladder, imported — the exemplars are held to the exact
    rule ``join_drafts`` holds the agent to, so the two cannot drift apart
    unnoticed. An exemplar quoting text that appears nowhere would teach the
    one thing this whole feature exists to prevent.
    """
    unfindable = [
        (lane, quote.text)
        for proposal in exemplar_proposals(framework, lane)
        for quote in proposal.quotes
        if (system := system_for_label(quote.source_label)) is not None
        and not verify_quote(quote.text, source_block(system)[1])
    ]
    assert not unfindable


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_exemplar_quote_labels_match_the_block(framework, lane):
    """A quote resolves to a source, which here is the block's declared label.

    With two worked systems the label is load-bearing rather than decorative:
    it is what picks the block the quote above is verified against, exactly as
    a real draft's ``source_label`` picks one of the job's sources.

    Two things, because a label can be wrong in two ways. It may name no
    shipped block at all, and — when the draft cites elements — it may name the
    *other* system's block, which is the cross-system mixing ``owning_system``
    exists to forbid arriving through the quote instead of the IDs.
    """
    unknown = [
        (lane, quote.source_label)
        for proposal in exemplar_proposals(framework, lane)
        for quote in proposal.quotes
        if system_for_label(quote.source_label) is None
    ]
    assert not unknown

    crossed = [
        (lane, quote.source_label)
        for proposal in exemplar_proposals(framework, lane)
        if (owner := owning_system(proposal)) is not None
        for quote in proposal.quotes
        if system_for_label(quote.source_label) != owner
    ]
    assert not crossed


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_exemplar_evidence_refs_are_in_the_exemplar_catalog(framework, lane):
    """Every ID an exemplar cites is one its own worked system offers.

    Membership, exactly as the resolver asks it. The reference lint beside this
    one covers ``affected_element_ids`` only, so without this the surface an
    agent copies from would be unchecked — and an exemplar composing an ID that
    resolves against nothing teaches an agent to do the same, at the one seam
    where that fails the whole lane.
    """
    dangling = [
        (lane, ref)
        for proposal in exemplar_proposals(framework, lane)
        if (owner := owning_system(proposal)) is not None
        for ref in proposal.evidence_refs
        if ref not in set(catalog(owner))
    ]
    assert not dangling

    # A draft citing no element cannot name a system, so a reference on one has
    # nothing to be checked against. None ship, and one arriving would be a
    # draft resting on a fact about a system it never identified.
    uncited = [
        (lane, proposal.evidence_refs)
        for proposal in exemplar_proposals(framework, lane)
        if owning_system(proposal) is None and proposal.evidence_refs
    ]
    assert not uncited


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
        for framework, lane in EXEMPLAR_LANES
        for proposal in exemplar_proposals(framework, lane)
    }
    unworked = [name for name, body in exemplar_systems().items() if body not in worked]
    assert not unworked, f"exemplar systems {unworked} are shown but never worked"


@pytest.mark.parametrize("framework,lane", EXEMPLAR_LANES)
def test_exemplar_file_within_token_cap(framework, lane):
    text = PACKAGE_LOADERS[framework].load(lane_exemplars_doc(lane))
    assert estimate_tokens(text) <= TOKEN_CAPS["package/lane_exemplars"]


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


@pytest.mark.parametrize("category", STRIDE_CATEGORIES)
def test_composed_analyze_prompt_within_the_sum_of_its_caps(category):
    """Composition adds joins, not content.

    ``COMPOSED_ANALYZE_CAP`` is the three part caps added up, so it cannot bind
    before any of them and no part cap can be one this makes unreachable. What
    is left for it to catch is text :func:`compose_analyze_prompt` introduces
    itself — a separator, a heading, a framing sentence — which no part cap
    watches because no file holds it.
    """
    composed = compose_analyze_prompt(loader, package_loader, category)
    assert estimate_tokens(composed) <= COMPOSED_ANALYZE_CAP


def test_the_verb_menu_in_the_output_contract_is_the_vocabulary():
    """``frameworks/stride/output.md`` carries exactly ``actions.menu()``.

    Without this the menu is a static copy that happens to match. A verb added
    to :data:`~stride_service.actions.ActionVerb` would reach the response
    schema — so the provider would accept it — and never reach the prompt, so no
    agent would learn the distinction exists. The field would be enforceable and
    unfillable, which is worse than either alone.

    ``actions.menu()``'s docstring claims it is built rather than written out.
    This is what makes that claim true rather than an intention.

    **Line by line rather than a substring test.** ``menu() in contract`` passes
    when a verb is *appended* to the last family, because the generated text is
    still a prefix of the altered line — which is exactly the drift direction a
    new verb takes. Comparing the family lines as a list catches an addition, a
    removal and a reordering alike.
    """
    contract = (PACKAGE_DIR / "output.md").read_text(encoding="utf-8")
    # ``- *family*:`` and not ``- **`field`**``, which a ``startswith("- *")``
    # would also take: one asterisk, a word, then the colon.
    family_line = re.compile(r"^- \*[a-z-]+\*: ")
    in_contract = [line for line in contract.splitlines() if family_line.match(line)]
    assert in_contract == menu().splitlines(), (
        "the verb menu in frameworks/stride/output.md is not what"
        " stride_service.actions.menu() emits. Regenerate it:\n\n" + menu()
    )
