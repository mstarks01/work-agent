"""Which known name is a model's reference spelling of?

This module answers one question mechanically, and it is the companion to
:mod:`analysis_service.grounding`. That module decides whether a quoted span is
really in the source it names. This one decides whether an identifier a model
emitted is really one the job already holds. Both exist because a model asked to
echo a string back does not always echo it byte for byte, and neither difference
is a judgement anybody should spend a prompt on.

It is a leaf, and imports nothing from this package, because both the validity
gate and the critic's seams need it and the gate runs long before a threat
exists. The functions that apply it to a particular schema live with that
schema's own checks: draft and ruling references in
:mod:`analysis_service.critic`, and an element's ``source_label`` in
:func:`~analysis_service.system_model.normalize_element_ids`.

It is not a resolver. It never finds the element a reference meant. It only
recognizes the one it already names, under a spelling the job's own construction
rules make equivalent. A reference that names nothing comes back empty, and the
caller's check reports it exactly as before.

The fold is two rungs, case and whitespace. The quote ladder's rungs are
concessions bought with measured precision. These two are almost always free,
and the uniqueness guard, rather than the argument for why, is what makes them
safe.

The argument comes first, because it says how often the guard binds. An element
ID is ``derive_element_id``'s output, and every ID a validated model carries
equals it: the gate's ``id-mismatch`` rule enforces that for every element, on
every path, whether or not the caller asked for normalization. That output runs
through :func:`~analysis_service.system_model.normalize_name`, which lowercases
the name and maps every character outside ``[a-z0-9]`` to ``-``. An ID therefore
carries neither an uppercase character nor a space, and folding one cannot merge
two elements a run could otherwise tell apart.

There is one hole, and it is why the guard exists rather than decorating the
module. ``normalize_name`` raises on a name that slugs to nothing, and the gate
answers that by skipping ``id-mismatch`` for that element rather than guessing.
Two elements named ``"!!!"`` and ``"???"`` may therefore carry ``process:FOO``
and ``process:foo`` and pass. Under a fold alone they would be one element.
Under the guard, the reference matches two known spellings, resolves to neither,
and is left for the caller's check exactly as an unresolvable reference is.

Source labels are the caller's bytes rather than derived slugs, so the argument
does not hold for them at all. ``"System description"`` and ``"SYSTEM
DESCRIPTION"`` are two distinct labels a job may legally carry at once. That is
the same situation the hole produces, met by the same guard, which is what lets
one function serve both without either being a special case.

Two further rungs are refused, for the reason ``grounding`` refuses
punctuation-blindness: a rung nobody has measured is a precision cost nobody has
priced.

* Bare slug — reading ``store:web-app`` as ``process:web-app`` because only one
  element has that slug. The referent is unambiguous, but the prefix is not
  spelling. An agent that filed a process as a data store reasoned about a
  different kind of thing, and a threat's meaning turns on which it was.
* Name to ID — turning ``"Web App"`` into ``process:web-app`` through
  ``normalize_name``. It is plausible, and every element in the prompt is
  already spelled as its ID, so nothing has shown a model doing this. It is the
  rung to add first if a real run ever produces one.
"""

from __future__ import annotations

from collections.abc import Collection


def fold(reference: str) -> str:
    """The spelling-insensitive key two references share iff they name one thing."""
    return " ".join(reference.split()).casefold()


def canonical(reference: str, known: Collection[str]) -> str:
    """The spelling in ``known`` that ``reference`` names, or ``""`` for none.

    Exact first, so a reference that is already canonical never depends on the
    fold. Then the fold, and **only when exactly one known spelling shares it**:
    two candidates mean the reference does not determine which, and guessing
    between them is the one thing this module must not do.
    """
    if reference in known:
        return reference
    folded = fold(reference)
    matches = [name for name in known if fold(name) == folded]
    return matches[0] if len(matches) == 1 else ""


def snap(reference: str, known: Collection[str]) -> str:
    """``reference`` in its canonical spelling, or unchanged when it names none.

    Left alone rather than blanked, because the caller's check is what reports
    an unresolvable reference and it can only name what it was given. Blanking
    would turn "cites an element the model does not contain" into a shape error
    about an empty string.
    """
    return canonical(reference, known) or reference


__all__ = ["canonical", "fold", "snap"]
