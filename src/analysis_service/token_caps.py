"""Every token cap over the static instruction text, in one table.

A cap here is a drift alarm rather than a budget. It rations nothing. It makes a
size change visible in review, and it fails the lint when one file grows past
what the alarm allows. Raising a cap costs a one-line edit and needs no
argument, because no measurement in this repository says a shorter instruction
finds more threats. ADR 0016 holds that reasoning.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path

from analysis_service.frameworks import (
    CRITIC_DOC,
    DISCLAIMER_DOC,
    OUTPUT_DOC,
    PACKAGES,
    SEVERITY_RUBRIC_DOC,
)

__all__ = [
    "COMPOSED_ANALYZE_CAP",
    "COMPOSED_EXTRACT_COMPACT_CAP",
    "JOB_VARYING_DIRS",
    "TOKEN_CAPS",
    "alarm_at",
    "covered_assets",
    "prompt_key",
]

#: Headroom the alarm leaves over the largest asset of a kind: a tenth, rounded
#: up to the next hundred.
#:
#: Proportional rather than fixed, so the alarm means the same thing on a 600
#: token file and a 3200 token one — an edit smaller than a tenth of the file
#: passes, and a new block trips it. A fixed allowance gives ``repair.md`` room
#: for a paragraph and the ASVS authentication chapter room for one sentence.
HEADROOM_FRACTION = 0.1


def alarm_at(largest: int) -> int:
    """The cap a kind whose largest asset is ``largest`` tokens gets.

    Call this when the lint fails, write the answer into :data:`TOKEN_CAPS`,
    and say in the commit message what the new text buys. Nothing has to leave
    to make room for it.
    """
    return math.ceil(largest * (1 + HEADROOM_FRACTION) / 100) * 100


def prompt_key(name: str) -> str:
    """The table key for one of the five shared prompt bodies."""
    return f"prompts/{name}"


#: Every capped kind of static instruction text, with the cap it alarms at.
#:
#: Each value is :func:`alarm_at` over the largest shipped asset of that kind at
#: the time it was last set. The lint checks a band rather than the exact value:
#: the file must fit under its cap, and the cap must not exceed twice the file,
#: because a cap sitting far above its content alarms at nothing.
#:
#: The package half is keyed by *kind*, not by package. A cap per package would
#: be a table that answers for the frameworks somebody already wrote.
TOKEN_CAPS: dict[str, int] = {
    # The five shared bodies, under ``prompts/``.
    #
    # ``analyze`` raised from 4500 and ``critic`` from 2400 for ADR 0039 rule
    # 4, which binds these two readers and nothing else: an undecidable
    # crossing confers eligibility for analysis and establishes nothing, so a
    # lane agent rests the claim on a fact the model states and a critic
    # refuses the crossing as a premise. The code already carried the rule —
    # the catalog omits such a crossing and ``crossing_facts`` marks it — and
    # neither prompt told its reader what the ``decided`` field it reads means.
    #
    # ``critic`` raised again to 3000 for the two evidence keys the view now
    # carries (#1082). ``unverified_quotes`` names a quote the service looked
    # for and did not find — it renders in ``grounds`` whatever the search
    # answered, and the critic was told every quote had matched;
    # ``assertion_facts`` resolves an assertion ground, whose identity digests
    # the value and the scope, so the critic was asked whether a claim follows
    # from a fact it could not read. Both are computed rather than drafted,
    # and both need a sentence saying how to rule with them.
    # ``analyze`` raised again to 5600 for what an absence establishes (#1110).
    # The bullet said how to write an ``absent_elements`` entry and never what
    # one licenses, so a lane read the service's confirmation — no element
    # names this term — as a verified fact about the deployment. Seven of ten
    # repudiation findings in the first review sitting could not be judged for
    # that reason. The two sentences added say an absence rules a claim out and
    # never holds one up, and that a claim asserting nothing anywhere provides
    # a property rests on a fact that holds everywhere or is written as what
    # cannot be produced. Both are properties of a claim rather than of a
    # package, so they sit here and not in a framework's contract.
    "prompts/analyze": 5600,
    "prompts/critic": 3000,
    "prompts/recritic": 1100,
    # Raised from 2900 for the naming rule in rule 3. The extraction sweep of
    # 2026-09-12 lost 98 blessed elements by ID, 59 of them to a name the model
    # chose differently — a plural, an expanded abbreviation, a qualifier the
    # text never attached — and 38 of the 39 lost flows ran between an endpoint
    # that had itself drifted.
    #
    # Raised again to 3600 for two conventions the reference was applying and
    # the prompt never stated (#925). `data_classification` now names its four
    # tiers and says a tier is an inference that takes an assumptions entry;
    # `interface_kind` now says the test is the interface a caller programs
    # against, so an RPC service is non-web over any transport. Both were
    # decided inside the corpus and nowhere else, which graded every extraction
    # against rules it was never given — the defect the audit is about, in the
    # contract rather than in the code.
    #
    # Raised again to 4100 for the one shape rule 3 had two answers for
    # (#1040). "Name it as the text does" and "name two same-named elements
    # apart" cannot both be obeyed where a source genuinely calls two things
    # by one name, so the rule now states what to write: the text's own
    # distinguishing word in front, and the shared name in `notes`.
    "prompts/extract": 4100,
    # The facts-first body (#1003 arm B). It carries the reading rules a second
    # time rather than appending to `extract.md`, because the two routes read
    # the same sources and write different things: one emits a System Model and
    # one emits handles, so there is no shared body for a delta to ride on. The
    # role and predicate tables beside it are rendered from `factbundle.ROLES`
    # and `assertions.REGISTRY`, so neither moves this number.
    "prompts/extract-facts": 2600,
    # The split facts-first route's two bodies (#1003 arm E), which together
    # answer whether the gap between the arms is the reading order or the
    # number of calls the work is spread over. Each carries one table: the
    # inventory call writes no fact and the rows call names no mention, so
    # neither is paid for the other's vocabulary.
    #
    # ``extract-inventory`` raised from 1700 for the reading rules (#1082). It
    # is the pass that decides what exists, and it carried none of them, while
    # `extract-rows.md` — which may not add a mention or take one away —
    # carried all six. A planned queue, a question and a withdrawn statement
    # each reached the closed inventory with nothing later able to remove it.
    "prompts/extract-inventory": 1900,
    "prompts/extract-rows": 2000,
    # The compact transport's delta, appended after the body above. It is the
    # whole cost of the route on the input side, paid on every extraction call
    # and cacheable, against the output it removes — see
    # :mod:`analysis_service.compact`.
    #
    # Raised from 600 for `compact-v4`'s flow-ref rule. Version 3 left a flow's
    # ref to the model and it derived one from the endpoints, so two flows
    # between one pair collided and the route failed its gate on
    # `duplicate-ref`. The paragraph that fixes it costs about 90 input tokens
    # against 1.04% of the corpus emission it removes, on ADR 0016's reading
    # that a cap here alarms rather than rations.
    "prompts/extract-compact": 750,
    # Raised from 900 for the `duplicate-id` step (#1040). The repair pass is
    # the one reader that sees that code, and it had no rule for it: its issue
    # list named an asset tag, an enum, an endpoint and a trust zone, and
    # "change nothing the issues do not cite" forbade the flows a rename
    # carries.
    #
    # Raised again to 1200 for three contract corrections (#1082). The prompt
    # listed `trust_zone` among the fields whose schema forbids `unknown`,
    # which ADR 0039 reversed, so repair was told to invent a placement the
    # gate accepts as absent; it pointed at "extraction rule 3's exception"
    # without carrying extraction's text; and it said "the same shape as
    # extraction", which is false on the compact route. It also now says which
    # half of "change nothing the issues do not cite" the service enforces,
    # because `restore_unimplicated` restores whole elements and never a field
    # on an implicated one.
    "prompts/repair": 1200,
    # The source-driven review body (#1003 arms C and D). The role and predicate
    # tables beside it are rendered, as they are for the facts-first body, so
    # neither moves this number.
    "prompts/reread": 1500,
    # The assertion body alone. The predicate table beside it is rendered from
    # `assertions.REGISTRY` rather than written here, so a predicate added
    # tomorrow moves the composed instruction and never this file.
    #
    # Raised from 1700 for the subject rule (#961 step 6). On case 01 the model
    # found 8 of 23 signed rows in every run, and the 13 it never found all sat
    # on a principal, a credential or a zone: subjects with no element, which
    # the procedure named only in passing. The body now lists them as subjects
    # first, and puts a credential on the principal that presents it.
    "prompts/assert": 2000,
    # One package's own text, under ``frameworks/<name>/``.
    #
    # ``critic`` raised from 1200, which STRIDE's critic text had sat one token under for
    # four checkpoint rounds. It said the service "already matched every quote
    # against the source it names", and `_verify_quotes` marks a quote it could
    # not find and keeps the draft, so the sentence was false of exactly the
    # draft that needed the critic most (#1082).
    f"package/{CRITIC_DOC}": 1400,
    # Raised from 200 for what ASVS's disclaimer had to say about a `gap`
    # (#1082). It said every claim asserts that the text "does not settle" the
    # requirement, which describes one of the three directions ADR 0028 gives
    # a draft and denies the one the report calls a gap.
    f"package/{DISCLAIMER_DOC}": 300,
    # Raised from 1100 for two contract corrections in ASVS's (#1082). The
    # `needs_evidence` rule read as "would a sentence prove the control", which
    # nothing ever does, so a question the submitter could answer routed to
    # `config` and left the report as unreachable rather than as a request; and
    # "rule on every requirement at or below the level" contradicted the scope
    # line's own list of units code ruled out. Both now say which reading wins.
    f"package/{OUTPUT_DOC}": 1400,
    f"package/{SEVERITY_RUBRIC_DOC}": 900,
    "package/lane_skill": 3600,
    "package/lane_exemplars": 1600,
    # One instruction a lane reads last, in its user turn rather than its
    # instruction, so COMPOSED_ANALYZE_CAP does not count it (ADR 0030).
    "package/lane_closing": 120,
    # Assembled rather than loaded: the critic's lane-boundary digest.
    "package/lane_digest": 2200,
    # The shared technology packs, under ``domains/``.
    "domain/pack": 800,
}

#: Package subdirectories whose files ride in the job-varying block, so the
#: alarm rule does not reach them. Their caps live with the retrieval ceiling
#: they answer, in ``tests/test_knowledge_lints.py``.
JOB_VARYING_DIRS = frozenset({"notes", "cases"})


def _package_key(relative: Path) -> str:
    """The table key for one file under ``frameworks/<name>/``."""
    if relative.parts[0] == "lanes":
        return f"package/lane_{relative.stem}"
    return f"package/{relative.stem}"


def covered_assets(frameworks_dir: Path) -> Iterator[tuple[Path, str]]:
    """Every package file the alarm covers, with the key it resolves to.

    Walks the **registered** packages rather than the directory listing, so a
    tree a deployment does not carry cannot satisfy the lint, and a package
    ``PACKAGES`` names cannot escape it. A file whose key is absent from
    :data:`TOKEN_CAPS` raises here rather than passing quietly — which is the
    whole reason the caps became a table.
    """
    for name in PACKAGES:
        for path in sorted((frameworks_dir / name).rglob("*.md")):
            relative = path.relative_to(frameworks_dir / name)
            if relative.parts[0] in JOB_VARYING_DIRS:
                continue
            key = _package_key(relative)
            if key not in TOKEN_CAPS:
                raise KeyError(f"{path} resolves to uncapped kind {key!r}")
            yield path, key


#: The whole instruction the compact extraction route reads: the shared body
#: plus its transport delta. Derived rather than written down, for the reason
#: :data:`COMPOSED_ANALYZE_CAP` is: a hand-set number would have to be argued
#: below the sum of its parts, and then one part's cap could never be reached.
COMPOSED_EXTRACT_COMPACT_CAP = (
    TOKEN_CAPS[prompt_key("extract")] + TOKEN_CAPS[prompt_key("extract-compact")]
)

#: The whole instruction one lane agent reads, as the caps bound it: the shared
#: body, the package's output contract, then that lane's exemplars.
#:
#: Derived rather than written down, and that is the point. The number this
#: replaces was set by hand and had to be argued *below* the sum of its parts,
#: so it bound first and a body cap it could not accommodate was a cap nothing
#: could reach. A sum cannot do that. What the lint over it still catches is
#: composition adding text of its own — the joins, not the content, since every
#: part already alarms on its own.
COMPOSED_ANALYZE_CAP = (
    TOKEN_CAPS[prompt_key("analyze")]
    + TOKEN_CAPS[f"package/{OUTPUT_DOC}"]
    + TOKEN_CAPS["package/lane_exemplars"]
)
