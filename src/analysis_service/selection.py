"""One reader of a job's framework selection, for every entry point.

A job names the frameworks it wants and the options each one needs. Three
entry points read that list — the HTTP route, the first-run web app and the
in-process engine — and each reading it its own way would let the route refuse
a repeated name while the web app collapses one silently and the engine does
not look. Two readers of one rule drift while both stay green (CLAUDE.md, *One rule,
one reader*), so the rule lives here and every entry point calls it, then
translates :class:`SelectionError` into its own refusal — a 422, a 400, an
:class:`~analysis_service.engine.EngineInputError`.

Four conditions, in the order a caller can act on them. The list is
non-empty; no name repeats; every name is one ``carried`` holds; and each
package's own options model accepts the options it was given. The first three
are about names alone and live in :func:`resolve_names`, because one seam holds
names and no options: a built graph is looked up by the frameworks it runs, and
two jobs naming the same frameworks with different options share one. A reader
of that seam that checked options would refuse every package whose options
carry a required field, on every job, whatever the job said. A repeat is
refused rather than collapsed because ``analyses`` is a list in the report so
a dropped block is visible, and de-duplicating here would hand back one block
for two the caller asked for — the same invisible loss, one layer earlier.

Order is the caller's and is preserved. It is the order the report's blocks
carry, and the envelope checks the two agree; an entry point that prefers
another order sorts what this returns.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Protocol, cast

from pydantic import ValidationError

from analysis_service.claims import FrameworkName
from analysis_service.frameworks import package_for
from analysis_service.report import FrameworkSelection


class Requested(Protocol):
    """What every entry point holds before the selection is checked: a name as
    the caller spelled it, and the options beside it. A wire request carries a
    free string, because which names exist is a property of the deployment
    rather than of the schema; a :class:`~analysis_service.report.FrameworkSelection`
    carries a narrowed one. Both fit, and the reader narrows the first.
    """

    @property
    def name(self) -> str: ...

    @property
    def options(self) -> Mapping[str, Any]: ...


class SelectionError(ValueError):
    """A framework selection this install cannot run, with the sentence saying why.

    A ``ValueError`` so an entry point that already answers one with its own
    refusal needs no second branch. The message names what is wrong — a
    repeated name, a name not carried, the option fields at fault — and never
    an option's value: a pydantic report of a caller's own body is the
    caller's to read from the exception's cause.
    """


def resolve_names(
    carried: Collection[FrameworkName], names: Sequence[str]
) -> list[FrameworkName]:
    """The names as the job will carry them, or the :class:`SelectionError` refusing them.

    The three name conditions and nothing about options. This is what a seam
    holding bare names reads — :meth:`~analysis_service.deployment.Deployment.selection`,
    which looks a built graph up by the frameworks it runs — and what
    :func:`resolve_selection` reads first, so the two cannot disagree about a
    name.
    """
    if not names:
        raise SelectionError(
            "a job must select at least one framework;"
            f" this install carries {', '.join(carried)}"
        )
    repeated = sorted(name for name, count in Counter(names).items() if count > 1)
    if repeated:
        raise SelectionError(
            f"frameworks repeats {', '.join(repeated)}; name each framework at most once"
        )
    unknown = [name for name in names if name not in carried]
    if unknown:
        raise SelectionError(
            f"this install does not carry {', '.join(unknown)};"
            f" it carries {', '.join(carried)}"
        )
    # The membership check above is the narrowing: a name that reaches here
    # is one ``carried`` holds, and a carried name is a FrameworkName by
    # construction. The cast says so to the type checker, which cannot read a
    # membership test over a Literal.
    return [cast(FrameworkName, name) for name in names]


def resolve_selection(
    carried: Collection[FrameworkName], requested: Sequence[Requested]
) -> list[FrameworkSelection]:
    """The selection as the job will carry it, or the :class:`SelectionError` refusing it."""
    names = resolve_names(carried, [entry.name for entry in requested])
    narrowed = [
        (name, dict(entry.options))
        for name, entry in zip(names, requested, strict=True)
    ]
    # Validated against the package's own options model, which declares no
    # defaulted field — so an option this framework requires and the caller
    # omitted is refused here rather than filled in with a value the caller
    # never chose. The message names the fields at fault and never their
    # values: a field is the package's own declaration, safe to show and the
    # only way a submitter learns which option they left out; a value is the
    # caller's body, which the cause carries for a local caller to read.
    for name, options in narrowed:
        try:
            package_for(name).options.model_validate(options)
        except ValidationError as exc:
            fields = sorted(
                {
                    ".".join(str(part) for part in error["loc"]) or "options"
                    for error in exc.errors()
                }
            )
            raise SelectionError(
                f"options for framework {name!r} are invalid: {', '.join(fields)}"
            ) from exc
    return [
        FrameworkSelection(name=name, options=options) for name, options in narrowed
    ]
