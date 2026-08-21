"""The ASVS framework package: a published standard, as a package.

Nine members and a text root at ``frameworks/asvs/``, exactly like STRIDE. What
differs is what stands behind them. STRIDE is a method, so its ``version`` names
this repo's ruleset and it carries no catalog. ASVS is a published standard, so
``version`` names the standard's own release and the 345 requirements live in
:mod:`stride_service.frameworks.asvs.catalog` as this package's private data.

**One lane is one chapter.** The standard's own applicability guidance operates
on a chapter — "for a machine-to-machine API, the requirements in chapter V3
related to web frontends will not be relevant" — so a lane that is a chapter puts
the standard's unit and this service's unit in the same place. The cost is real:
one lane is one **Model Tier** call, so an ASVS job runs 17 ``strong``-tier calls
against STRIDE's 6.

**This package reports no pass.** ASVS verification needs source code,
configuration and the development team, and a job here carries prose. See
:mod:`stride_service.frameworks.asvs.record` for what the three **Verdict**
states carry instead, and ``disclaimer.md`` for what the report's reader is told.
"""

from __future__ import annotations

from types import MappingProxyType

from stride_service.frameworks import (
    FrameworkPackage,
    IdRule,
    KnowledgeTables,
)
from stride_service.frameworks.asvs.catalog import (
    ASVS_VERSION,
    CHAPTER_NUMBERS,
    LANES,
    is_published_requirement,
)
from stride_service.frameworks.asvs.record import (
    ASVS_ID_FORMAT,
    AsvsOptions,
    DraftRequirementRuling,
)
from stride_service.frameworks.asvs.rules import RULES, asvs_precondition

__all__ = ["ASVS", "AsvsOptions"]


ASVS = FrameworkPackage(
    name="asvs",
    version=ASVS_VERSION,
    lanes=LANES,
    rules=RULES,
    record=DraftRequirementRuling,
    id_rule=IdRule(
        # The standard's own version-safe reference, composed from the lane's
        # chapter number and the agent's ``<section>.<requirement>`` key. The
        # chapter therefore appears in the ID, in the lane and in the record from
        # one call, so the three cannot disagree.
        template=ASVS_ID_FORMAT,
        prefix=CHAPTER_NUMBERS,
        lane_field="chapter",
        # The one thing the key's shape cannot say. ``99.99`` is as well-formed
        # as ``2.1``, so without this the service composes the standard's own
        # version-safe reference for a requirement the standard does not
        # publish. STRIDE declares no predicate because it mints its own IDs.
        known=is_published_requirement,
    ),
    options=AsvsOptions,
    precondition=asvs_precondition,
    # Both tables ship empty, which the contract makes a written statement rather
    # than an omission: this package carries no **Reference Note** and no
    # **Worked Case** yet, and the gate passes it vacuously.
    knowledge=KnowledgeTables(notes=MappingProxyType({}), cases=MappingProxyType({})),
)
