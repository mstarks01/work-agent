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
    "prompts/critic": 2100,
    "prompts/recritic": 1100,
    "prompts/extract": 2900,
    "prompts/repair": 700,
    # One package's own text, under ``frameworks/<name>/``.
    f"package/{CRITIC_DOC}": 700,
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
