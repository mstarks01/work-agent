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
    # ``analyze`` and ``critic`` carry ADR 0039 rule 4, which binds these two
    # readers and nothing else: an undecidable crossing confers eligibility for
    # analysis and establishes nothing, so a lane agent rests the claim on a
    # fact the model states and a critic refuses the crossing as a premise. The
    # catalog omits such a crossing and ``crossing_facts`` marks it; each
    # prompt tells its reader what the ``decided`` field it reads means.
    #
    # ``critic`` also says how to rule with the two evidence keys the view
    # carries (#1082). ``unverified_quotes`` names a quote the service looked
    # for and did not find — it renders in ``grounds`` whatever the search
    # answered. ``assertion_facts`` resolves an assertion ground, whose
    # identity digests the value and the scope, so the critic can read the
    # fact a claim rests on. Both are computed rather than drafted.
    #
    # ``analyze`` says what an absence establishes (#1110): an absence rules a
    # claim out and never holds one up, and a claim that asserts nothing
    # anywhere provides a property rests on a fact that holds everywhere or is
    # written as what cannot be produced. Without it, a lane reads the
    # service's confirmation — no element names this term — as a verified fact
    # about the deployment; seven of ten repudiation findings in the first
    # review sitting could not be judged for that reason. Both rules are
    # properties of a claim rather than of a package, so they sit here and not
    # in a framework's contract.
    #
    # ``critic`` and ``recritic`` carry the submitted sources (ADR 0057). Each
    # body holds ``{input_text}``, the sentence that says the sources are data
    # and not instruction, and, in the critic, the rule that a source fact can
    # defeat a draft and never closes an open fact.
    "prompts/analyze": 5600,
    "prompts/critic": 3500,
    "prompts/recritic": 1400,
    # ``extract`` carries the naming rule in rule 3. The extraction sweep of
    # 2026-09-12 lost 98 blessed elements by ID, 59 of them to a name the model
    # chose differently — a plural, an expanded abbreviation, a qualifier the
    # text never attached — and 38 of the 39 lost flows ran between an endpoint
    # with a changed name.
    #
    # It also states two conventions the reference applies (#925).
    # `data_classification` names its four tiers and says a tier is an
    # inference that takes an assumptions entry; `interface_kind` says the test
    # is the interface a caller programs against, so an RPC service is non-web
    # over any transport. Without them, every extraction is graded against
    # rules it is never given.
    #
    # It gives rule 3 one answer for one shape (#1040). "Name it as the text
    # does" and "name two same-named elements apart" cannot both be obeyed
    # where a source genuinely calls two things by one name, so the rule states
    # what to write: the text's own distinguishing word in front, and the
    # shared name in `notes`.
    "prompts/extract": 4100,
    # The compact transport's delta, appended after the body above. It is the
    # whole cost of the route on the input side, paid on every extraction call
    # and cacheable, against the output it removes — see
    # :mod:`analysis_service.compact`.
    #
    # It carries `compact-v4`'s flow-ref rule. A model left to derive a flow's
    # ref from the endpoints collides two flows between one pair, and the
    # route fails its gate on `duplicate-ref`. The paragraph costs about 90
    # input tokens against 1.04% of the corpus emission it removes, on ADR
    # 0016's reading that a cap here alarms rather than rations.
    "prompts/extract-compact": 750,
    # ``repair`` carries the `duplicate-id` step (#1040). The repair pass is
    # the one reader that sees that code, and "change nothing the issues do not
    # cite" would otherwise forbid the flows a rename carries.
    #
    # It also states three contract points (#1082): `trust_zone` may be
    # `unknown` (ADR 0039), so repair does not invent a placement; it carries
    # the text of extraction rule 3's exception rather than a pointer to it;
    # and it does not claim "the same shape as extraction", which is false on
    # the compact route. It says which half of "change nothing the issues do
    # not cite" the service enforces, because `restore_unimplicated` restores
    # whole elements and never a field on an implicated one.
    "prompts/repair": 1200,
    # The assertion body alone. The predicate table beside it is rendered from
    # `assertions.REGISTRY` rather than written here, so a predicate added
    # tomorrow moves the composed instruction and never this file.
    #
    # It carries the subject rule (#961 step 6): principals, credentials and
    # zones are listed as subjects first, and a credential sits on the
    # principal that presents it. On case 01, without the rule, the model found
    # 8 of 23 signed rows in every run, and the 13 it never found all sat on
    # such a subject.
    "prompts/assert": 2000,
    # One package's own text, under ``frameworks/<name>/``.
    #
    # ``critic`` must not say the service "already matched every quote against
    # the source it names" (#1082): `_verify_quotes` marks a quote it could not
    # find and keeps the draft, so the sentence would be false of exactly the
    # draft that needs the critic most.
    f"package/{CRITIC_DOC}": 1400,
    # ASVS's disclaimer says what a `gap` means (#1082). A claim can take any
    # of the three directions ADR 0028 gives a draft, including the one the
    # report calls a gap, so "does not settle" alone does not describe every
    # claim.
    f"package/{DISCLAIMER_DOC}": 300,
    # ASVS's output contract states two readings (#1082). `needs_evidence`
    # asks whether the submitter could answer the question, not whether a
    # sentence would prove the control, so such a question routes to a
    # request and not to `config`. "Rule on every requirement at or below the
    # level" yields to the scope line's own list of units code ruled out.
    f"package/{OUTPUT_DOC}": 1400,
    f"package/{SEVERITY_RUBRIC_DOC}": 900,
    "package/lane_skill": 3600,
    # Four spoofing exemplars (#1295). One presents a held credential and
    # carries ``use-credential``, as ``output.md`` files it; one poses as a
    # caller without a credential, so the lane demonstrates both verbs.
    "package/lane_exemplars": 2000,
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
    :data:`TOKEN_CAPS` raises here rather than passing quietly — which is
    why the caps are a table.
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
#: Derived rather than written down, and that is the point. A hand-set number
#: would have to stay *below* the sum of its parts, so it would bind first, and
#: a body cap it could not accommodate would be a cap nothing could reach. A sum
#: cannot do that. What the lint over it still catches is
#: composition adding text of its own — the joins, not the content, since every
#: part already alarms on its own.
COMPOSED_ANALYZE_CAP = (
    TOKEN_CAPS[prompt_key("analyze")]
    + TOKEN_CAPS[f"package/{OUTPUT_DOC}"]
    + TOKEN_CAPS["package/lane_exemplars"]
)
