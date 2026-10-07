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
from analysis_service.markdown_loader import MarkdownLoader, MarkdownNotFoundError
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

    They can disagree in both directions. A gate that asks `is_file()`
    follows a symlink out of the package root that `MarkdownLoader.load`
    refuses, so a package passes startup and fails on its first job. A gate
    that takes `path.parent` as its root refuses a lane skill symlinked to
    another file inside the same package, which the loader accepts.

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
    # The third shape: a loop. Python 3.12's `resolve` raises `RuntimeError`
    # on one, not `OSError`, and both readers must answer "absent" rather
    # than raise through the startup gate or the first job.
    (root / "critic.md").symlink_to(root / "critic.md")

    loader = MarkdownLoader(root)
    for name in ("lanes/spoofing/skill", "lanes/spoofing/exemplars", "critic"):
        path = root / f"{name}.md"

        assert _readable(root, path) == loader.readable(name), name
    assert not loader.readable("critic")
    with pytest.raises(MarkdownNotFoundError):
        loader.load("critic")


# --- The facts that decide an undecidable precondition (ADR 0067) ------------


def test_precondition_facts_that_are_not_callable_fail_the_gate():
    """A paused job could not ask what decides the framework, so it never starts."""
    from dataclasses import replace

    package = replace(PACKAGES["stride"], precondition_facts=())

    with pytest.raises(FrameworkPackageError) as caught:
        validate_package(package, STRIDE_ROOT)

    assert "precondition_facts" in str(caught.value)


def _undecidable_models():
    """Every corpus model, and the shared model with nothing said about its
    interfaces or its transport (#1542 F1)."""
    from analysis_service.system_model import SystemModel

    models = [
        (case.name, SystemModel.model_validate_json((case / "model.json").read_text()))
        for case in sorted((PROJECT_ROOT / "evals" / "corpus").iterdir())
    ]
    silent = valid_model()
    for process in silent.processes:
        process.interface_kind = "unknown"
    for flow in silent.data_flows:
        flow.protocol = "unknown"
    return [*models, ("silent", silent)]


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_every_undecidable_precondition_offers_an_answer_that_decides_it(name):
    """**A package cannot be undecidable with no way for its owner to decide
    it.** Where the precondition reads ``undecidable``, the facts the package
    offers are open questions a paused job asks, and one answer to each, from
    the choices the question offers, decides the gate ``prepare`` runs. Vacuous
    for a package whose precondition is total, which is the point: the test
    binds the next package that can be undecidable."""
    from analysis_service.answer_forms import answer_choices
    from analysis_service.fact_answers import FactAnswer
    from analysis_service.fact_writes import answered_model, check_fact_answers

    package = PACKAGES[name]
    for label, model in _undecidable_models():
        if run_precondition(package, model) != "undecidable":
            continue
        offered = package.precondition_facts(model)
        assert offered, f"{name} offers nothing to decide {label}"
        decided = set()
        for choice_at in range(2):
            facts = []
            for ref in offered:
                choices = answer_choices(ref.key, model, None)
                value = choices[choice_at % len(choices)] if choices else "https"
                facts.append(FactAnswer(key=ref.key, value=value))
            check_fact_answers(facts, model, None)
            decided.add(run_precondition(package, answered_model(model, facts)))
        assert decided - {"undecidable"}, f"{name} stays undecidable on {label}"
