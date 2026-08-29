"""Which domain packs a System Model earns, decided deterministically.

``skills/domains/`` holds subject-matter packs — HTTP APIs, OAuth/OIDC,
multi-tenancy, data stores — that sharpen a category agent on architectures it
would otherwise reason about generically. Loading all of them into every
analysis is the failure this module exists to prevent: it is the largest block
of text in the longest prompt the graph sends, and most of it would be about a
system nobody submitted.

Selection is **structural and keyword-based, and it is deliberately shallow.**
A detector reads a handful of the model's own free-text fields for terms that
name a technology, and that is all it does. It is not deciding anything about
security; it is deciding which reference material is on the desk. A false
positive costs tokens, a false negative costs a sharper prompt, and neither
can produce, suppress or ground a finding — the pack text is repo-authored and
identical for every job that selects it.

That last point is the security argument too (OWASP LLM01). Caller text
influences *which* pack loads and never *what a pack says*: names are matched
against a closed set defined here, the loader only ever reads
``skills/domains/<name>.md`` for a name in :data:`DETECTORS`, and no caller
byte reaches the composed skill text through this path.

:data:`MAX_PACKS` caps the selection. When more packs match than that, the ones
with the most matching elements win — the ranking is by evidence in the model,
with declaration order as the tie-break, so the choice is stable across runs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from analysis_service.system_model import Element, SystemModel

__all__ = ["DETECTORS", "MAX_PACKS", "pack_evidence", "select_domain_packs"]

# At most two packs per analysis. A pack runs to ~700 tokens and rides in the
# job-varying block of six parallel instructions, so the third pack costs more
# than a fourth of the whole analysis budget and buys the least relevant of the
# three. Two is a budget, not a finding about how many domains a system has.
MAX_PACKS = 2

# The element fields a detector reads: the ones that name technologies and
# protocols. Deliberately excludes ``notes``, ``description`` and
# ``source_excerpt`` — those carry submitter prose, where a passing mention of
# a technology the system does not use is common and would swing selection.
_SCANNED_FIELDS = ("technology", "protocol", "authentication", "name")

# pack name -> the terms that select it. Matched case-insensitively on word
# boundaries against the fields above, so ``sso`` does not fire on ``lasso``.
# Each name must have a matching ``skills/domains/<name>.md``; the skill lints
# hold that true.
DETECTORS: dict[str, tuple[str, ...]] = {
    "http-api": (
        "http",
        "https",
        "rest",
        "grpc",
        "graphql",
        "webhook",
        "api gateway",
        "fastapi",
        "express",
        "flask",
        "django",
        "spring",
    ),
    "oauth-oidc": (
        "oauth",
        "oidc",
        "openid",
        "jwt",
        "sso",
        "single sign-on",
        "identity provider",
        "idp",
        "bearer token",
        "access token",
        "id token",
    ),
    "multi-tenant-saas": ("tenant", "tenancy", "workspace", "organization scope"),
    "databases": (
        "postgres",
        "postgresql",
        "mysql",
        "mariadb",
        "sql server",
        "oracle",
        "sqlite",
        "database",
        "mongodb",
        "dynamodb",
        "cassandra",
        "redis",
        "elasticsearch",
        "opensearch",
        "s3",
        "bucket",
        "object store",
        "blob storage",
    ),
}

_TERM_PATTERNS: dict[str, re.Pattern[str]] = {
    pack: re.compile(
        r"\b(?:" + "|".join(re.escape(term) for term in terms) + r")\b",
        re.IGNORECASE,
    )
    for pack, terms in DETECTORS.items()
}


def pack_evidence(model: SystemModel) -> dict[str, int]:
    """Each pack's match count: how many elements carry one of its terms.

    Counted per element rather than per term occurrence, so a single verbose
    ``technology`` string cannot outweigh a technology that four elements
    actually use. Packs with no match are absent rather than present at zero.
    """
    counts = {
        pack: sum(
            1 for element in model.elements() if pattern.search(_scanned(element))
        )
        for pack, pattern in _TERM_PATTERNS.items()
    }
    tenant_boundaries = sum(
        1
        for boundary in model.trust_boundaries
        if getattr(boundary, "kind", "") == "tenant"
    )
    # A ``kind: tenant`` boundary is the model *stating* multi-tenancy in its
    # own vocabulary rather than mentioning the word, so it counts like an
    # element match and does not depend on anyone spelling "tenant" in prose.
    counts["multi-tenant-saas"] += tenant_boundaries
    return {pack: count for pack, count in counts.items() if count}


def select_domain_packs(model: SystemModel) -> tuple[str, ...]:
    """The packs this model earns, most-evidenced first, capped at :data:`MAX_PACKS`.

    Ties break on :data:`DETECTORS` order, which is fixed in source, so two
    runs over the same model select the same packs in the same order and the
    composed instruction is byte-identical.
    """
    order = list(DETECTORS)
    evidence = pack_evidence(model)
    ranked = sorted(evidence, key=lambda pack: (-evidence[pack], order.index(pack)))
    return tuple(ranked[:MAX_PACKS])


def _scanned(element: Element) -> str:
    """The element's technology-naming fields, joined for one regex pass."""
    return " ".join(_field_values(element))


def _field_values(element: Element) -> Iterable[str]:
    for field in _SCANNED_FIELDS:
        value = getattr(element, field, None)
        if isinstance(value, str):
            yield value
