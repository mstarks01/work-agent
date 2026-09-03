"""The **Framework Package** contract's precondition: declared, and run.

Two checks in two places, because one needs a **Valid System Model** and the
other does not. :func:`~analysis_service.frameworks.validate_package` checks the
member is callable, beside the checks it already runs on the other eight.
:func:`~analysis_service.frameworks.run_precondition` checks what the member
*returns*, which nothing can know until a model exists.

Every test here builds its package from the shipped one
(:func:`tests.factories.package_whose_precondition`), so the precondition is the
only thing that differs from what this install actually carries.
"""

from __future__ import annotations

import pytest

from analysis_service.frameworks import (
    PACKAGES,
    PRECONDITION_RESULTS,
    FrameworkPackageError,
    PreconditionError,
    _readable,
    run_precondition,
    validate_package,
)
from analysis_service.markdown_loader import MarkdownLoader
from tests.factories import (
    PROJECT_ROOT,
    package_answering,
    package_whose_precondition,
    valid_model,
)

STRIDE_ROOT = PROJECT_ROOT / "frameworks" / "stride"


def test_the_three_states_are_the_contract_s_own():
    """The gate checks a return against the type, not against a second list."""
    assert set(PRECONDITION_RESULTS) == {"satisfied", "refuted", "undecidable"}


# --- The declaration check, at the gate --------------------------------------


def test_the_shipped_package_passes_the_gate():
    """The baseline: the new check does not refuse what this install carries."""
    validate_package(PACKAGES["stride"], STRIDE_ROOT)


def test_a_precondition_that_is_not_callable_fails_the_gate():
    """Nothing could ask this framework whether it applies, so it never starts."""
    package = package_whose_precondition("satisfied")

    with pytest.raises(FrameworkPackageError) as caught:
        validate_package(package, STRIDE_ROOT)

    assert "not callable" in str(caught.value)


# --- The return-state check, at the call site --------------------------------


@pytest.mark.parametrize("result", PRECONDITION_RESULTS)
def test_each_declared_state_passes_through(result):
    assert run_precondition(package_answering(result), valid_model()) == result


def test_a_state_the_contract_does_not_define_raises():
    """Named rather than guessed at: the message carries the package and the value."""
    with pytest.raises(PreconditionError) as caught:
        run_precondition(package_answering("probably"), valid_model())

    assert "'stride'" in str(caught.value)
    assert "'probably'" in str(caught.value)


@pytest.mark.parametrize("result", [None, True, "REFUTED", ""])
def test_an_unrecognised_value_is_never_read_as_a_refusal(result):
    """The failure mode this check exists for.

    Reading an undefined answer as ``refuted`` would drop a whole analysis the
    caller asked for, and the caller would read no sign of it. So every value
    outside the three raises, including the ones that look like a refusal and
    the ones that are merely falsy.
    """
    with pytest.raises(PreconditionError):
        run_precondition(package_answering(result), valid_model())


def test_a_precondition_that_raises_is_wrapped_with_the_package_that_raised():
    """An unwrapped exception says nothing about whose code it came from."""

    def explode(model):
        raise ZeroDivisionError("bad rule")

    with pytest.raises(PreconditionError) as caught:
        run_precondition(package_whose_precondition(explode), valid_model())

    assert "'stride'" in str(caught.value)
    assert isinstance(caught.value.__cause__, ZeroDivisionError)


def test_a_precondition_error_is_a_package_error():
    """Same subject as the gate's refusals — a carried package is ill-formed.

    It fires at a call site rather than at construction only because a
    precondition reads a model, so the deployment gate cannot reach it.
    """
    assert issubclass(PreconditionError, FrameworkPackageError)


def test_the_gate_and_the_loader_answer_the_same_question(tmp_path):
    """The two readers of "is this file mine to read", asked together.

    They have now disagreed in both directions. The gate first asked
    `is_file()`, which follows a symlink out of the package root that
    `MarkdownLoader.load` refuses — so a package passed startup and failed on
    its first job. Sharing the loader's rule fixed that and introduced the
    mirror image: the gate was handed `path.parent` as its root, so a lane
    skill symlinked to another file inside the same package was accepted by the
    loader and refused by the gate.

    Asked of both readers over the same paths, which is the only way this pair
    stays honest.
    """
    root = tmp_path / "pkg"
    (root / "lanes" / "spoofing").mkdir(parents=True)
    (root / "lanes" / "tampering").mkdir(parents=True)
    shared = root / "lanes" / "tampering" / "skill.md"
    shared.write_text("# shared\n", encoding="utf-8")
    (root / "lanes" / "spoofing" / "skill.md").symlink_to(shared)
    outside = tmp_path / "elsewhere.md"
    outside.write_text("# elsewhere\n", encoding="utf-8")
    (root / "lanes" / "spoofing" / "exemplars.md").symlink_to(outside)

    loader = MarkdownLoader(root)
    for name in ("lanes/spoofing/skill", "lanes/spoofing/exemplars"):
        path = root / f"{name}.md"

        assert _readable(root, path) == loader.readable(name), name
