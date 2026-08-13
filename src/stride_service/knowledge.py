"""The local corpus: reference notes and worked cases, retrieved by what fired.

A package's corpus is a version-controlled security library this service ships
and reads locally — no web access, no vector store, no embedding model, and no
retrieval that could return different text tomorrow for the same input. It holds
two kinds of document, different in standing rather than in subject:

* **notes** (``frameworks/<name>/notes/``) — security reference on one
  condition: what it means, what to ask about it, and what it is not. Analysis
  knowledge, of the same standing as a **Domain Pack**.
* **cases** (``frameworks/<name>/cases/``) — a worked judgement: a pattern, the
  threat considered, whether it was accepted or rejected, and what decided it.
  These are the reasoning the exemplars cannot carry, because an exemplar is a
  finished draft and half of these end in a rejection.

**Both belong to a Framework Package, and the retrieval key is why.** Selection
is a set intersection over the **Candidate** rules that fired, and a package owns
its rules — so a document the service stored would be a service-owned file that
only a package could select. A **Domain Pack** goes the other way for the same
test: it reads the **Valid System Model**'s own technology fields, which one
extraction fills for every framework, so its key is neutral and it stays in one
shared root.

The cost of that split, stated: a second package **re-authors any note it
wants**. It is smaller than it looks — a note's *"What to look for"* questions
are written for one framework's reading of the condition, and 7 of the 10 notes
here end by assigning a STRIDE lane.

**Retrieval is by fired rule, and that is the whole mechanism.** A lane agent's
leads are the deterministic candidates whose rules matched this model; the
documents it is given are the ones those same rules name. So a lane that
triggered nothing receives nothing, a lane looking at an unverified boundary
gets the note about identity at a boundary, and no job carries reference
material about a technology or a condition nobody's model exhibits. That is the
progressive disclosure the alternative — every document in every prompt — exists
to avoid, and it needs no scoring function, no index and no query: the model's
own structure selected it.

**The corpus is knowledge, never evidence, and nothing here can change that.**
A note explains what to ask; a case shows how someone reasoned. Neither is a
fact about the system under review, neither is in the evidence catalog, and
neither can be cited — the prompt says so and the resolution seam has no branch
that could accept one. What grounds a finding is unchanged: the submitter's
words, an ``unknown`` attribute, or a derived crossing.

**Caller text selects nothing** (OWASP LLM01). Selection reads rule IDs, which
come from code; a package's tables are closed; and a name outside them is never
loaded. As with :mod:`stride_service.domains`, no submitted byte reaches the
composed text through this path.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from stride_service.markdown_loader import MarkdownLoader

__all__ = [
    "MAX_CASES",
    "MAX_NOTES",
    "compose_cases",
    "compose_notes",
    "select_documents",
]

# Per lane, per job. Lanes run in parallel and each pays its own tokens, so
# these caps are multiplied by the lane count before they are a budget: two
# notes and one case per lane is roughly the size of the domain-pack block
# beside it. They are budgets rather than findings about how much material is
# relevant — the ranking below decides which of the matches survive them.
#
# **The service's, and identical for every package.** A cap a package set would
# be the same cost knob by another route, and it would let one framework spend
# more of a shared budget than another. The per-job corpus cost is now paid per
# (framework, lane), and nobody has measured it: no live sweep ever ran here.
MAX_NOTES = 2
MAX_CASES = 1


def select_documents(
    index: Mapping[str, tuple[str, ...]], fired: Collection[str], limit: int
) -> tuple[str, ...]:
    """The documents these fired rules earn, most-matched first, capped.

    Ranked by how many of the lane's fired rules name a document, with
    declaration order as the tie-break — fixed in the package's source, so two
    runs over one model select the same documents in the same order and the
    composed instruction is byte-identical. That stability is the same property
    :func:`~stride_service.domains.select_domain_packs` needs and for the same
    reason: an instruction that reordered between runs would make two otherwise
    identical jobs send different bytes.
    """
    fired_set = set(fired)
    matched = [
        (-hits, position, name)
        for position, (name, rules) in enumerate(index.items())
        if (hits := len(fired_set.intersection(rules)))
    ]
    return tuple(name for _, _, name in sorted(matched)[:limit])


def _compose(loader: MarkdownLoader, prefix: str, names: Sequence[str]) -> str:
    """The named documents' text, in selection order, or ``""`` for none.

    The empty string is what a lane earning nothing renders, deliberately
    rather than a "no documents selected" note: the prompt reads the block as
    optional material, and a sentence saying there is none is a sentence about
    nothing. It is also the common case for a lane whose rules all found
    nothing, which is not a defect.
    """
    if not names:
        return ""
    return "\n\n".join(loader.load(f"{prefix}/{name}").strip() for name in names) + "\n"


def compose_notes(loader: MarkdownLoader, names: Sequence[str]) -> str:
    """The selected reference notes, composed for one lane's prompt block."""
    return _compose(loader, "notes", names)


def compose_cases(loader: MarkdownLoader, names: Sequence[str]) -> str:
    """The selected worked cases, composed for one lane's prompt block."""
    return _compose(loader, "cases", names)
