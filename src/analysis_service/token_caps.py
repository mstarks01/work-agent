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
    "prompts/analyze": 4500,
    "prompts/critic": 2400,
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
    "prompts/extract": 3600,
    # The facts-first body (#1003 arm B). It carries the reading rules a second
    # time rather than appending to `extract.md`, because the two routes read
    # the same sources and write different things: one emits a System Model and
    # one emits handles, so there is no shared body for a delta to ride on. The
    # role and predicate tables beside it are rendered from `factbundle.ROLES`
    # and `assertions.REGISTRY`, so neither moves this number.
    "prompts/extract-facts": 2600,
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
    "prompts/repair": 900,
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
    f"package/{CRITIC_DOC}": 1200,
    f"package/{DISCLAIMER_DOC}": 200,
    f"package/{OUTPUT_DOC}": 1100,
    f"package/{SEVERITY_RUBRIC_DOC}": 900,
    "package/lane_skill": 3600,
    "package/lane_exemplars": 1600,
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
