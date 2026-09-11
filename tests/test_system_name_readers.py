"""The system name rule, and the two entry points held against each other.

``clean_system_name`` is the rule. The HTTP route, the in-process engine and
the stored report's own field are its three callers, and this file does the job
the parity suites do elsewhere: never ask whether a caller agrees with its own
expectation, ask whether the callers agree with each other.

A door can depart from the rule in two ways, and both are failures here. It can
admit a name the rule refuses, or refuse one the rule admits. It can also admit
the same names and store a different one — a door that keeps a caller's padding
and a door that trims it agree on every status code and still disagree about
what the report says.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from analysis_service.engine import EngineInputError
from analysis_service.jobs import PipelineRejected
from analysis_service.report import InputRef
from analysis_service.sources import MAX_SYSTEM_NAME_CHARS, Source, clean_system_name
from tests.test_api import auth, make_client, submission
from tests.test_engine import RecordingRunner, analyze, engine_for

# Written as escapes rather than as themselves: every one of these is invisible
# or reorders the line around it, which is the property that makes it worth
# refusing and would make this list unreadable in source.
BIDI_OVERRIDE = "Orders\u202egnirdrO"
ZERO_WIDTH = "Orders\u200bhidden"
NEXT_LINE = "Orders\u0085next line"
LINE_SEPARATOR = "Orders\u2028second line"

#: Every name whose treatment could turn on which door it arrives through,
#: plus the ordinary ones that must keep working. One list, both readers.
NAMES = [
    "Orders API",
    "  Orders API  ",
    "   ",
    "",
    "s" * MAX_SYSTEM_NAME_CHARS,
    "s" * (MAX_SYSTEM_NAME_CHARS + 1),
    "  " + "s" * MAX_SYSTEM_NAME_CHARS + "  ",
    "Orders\nCRITICAL forged",
    LINE_SEPARATOR,
    BIDI_OVERRIDE,
    ZERO_WIDTH,
    NEXT_LINE,
]


#: What a door did with a name: whether it admitted it, and the name it wrote
#: onto the job if it did. Both halves matter. Two doors that both admit
#: ``"   "`` still disagree if one stores three spaces and the other stores
#: nothing, and comparing only the refusals would call that agreement.
Outcome = tuple[bool, str | None]


def _recorder() -> RecordingRunner:
    """A runner that keeps the job record it was handed and analyses nothing.

    The record is the observable both doors share. What the run then returns is
    beside the point here, so it returns the cheapest valid outcome.
    """
    return RecordingRunner(PipelineRejected(issues=[]))


def _http_outcome(name: str) -> Outcome:
    """What the route did with ``name``, by its status and the job it queued."""
    runner = _recorder()
    client, _ = make_client(runner=runner)

    response = client.post(
        "/v1/jobs", json=submission(system_name=name), headers=auth()
    )

    assert response.status_code in {201, 422}, response.status_code
    if response.status_code == 422:
        return (False, None)
    return (True, runner.jobs[0].system_name)


def _engine_outcome(name: str) -> Outcome:
    """What the engine did with ``name``, by its refusal and the job it built."""
    runner = _recorder()
    try:
        analyze(engine_for(runner), "text", system_name=name)
    except EngineInputError:
        return (False, None)
    return (True, runner.jobs[0].system_name)


def _rule_outcome(name: str) -> Outcome:
    """What the shared rule says, in the shape the two doors are read in."""
    try:
        return (True, clean_system_name(name))
    except ValueError:
        return (False, None)


@pytest.mark.parametrize("name", NAMES)
def test_the_two_entry_points_agree(name: str) -> None:
    """The route and the engine refuse the same names and store the same names."""
    assert _http_outcome(name) == _engine_outcome(name)


@pytest.mark.parametrize("name", NAMES)
def test_neither_door_departs_from_the_rule(name: str) -> None:
    """Each caller answers to the rule, rather than to an expectation of its own."""
    expected = _rule_outcome(name)

    assert _http_outcome(name) == expected
    assert _engine_outcome(name) == expected


@pytest.mark.parametrize("name", ["   ", "", "\t\n "])
def test_a_name_that_is_only_padding_means_no_name(name: str) -> None:
    """Whitespace is not a name, so the report's own default fills the field.

    A length bound alone does not settle this: ``min_length`` counts padding.
    """
    assert clean_system_name(name) is None


def test_the_bound_measures_the_trimmed_name() -> None:
    """Padding cannot push a legal name over the bound, nor a long one under it."""
    padded = "  " + "s" * MAX_SYSTEM_NAME_CHARS + "  "

    assert clean_system_name(padded) == "s" * MAX_SYSTEM_NAME_CHARS
    with pytest.raises(ValueError, match="exceeds"):
        clean_system_name("s" * (MAX_SYSTEM_NAME_CHARS + 1))


def _stored_outcome(name: str) -> Outcome:
    """What the third door — a report read back — does with a stored name."""
    try:
        ref = InputRef.of(system_name=name, sources=[Source.description("a web app")])
    except ValidationError:
        return (False, None)
    return (True, ref.system_name)


def _stored_rule_outcome(name: str) -> Outcome:
    """What the rule says about a name a report may *carry*.

    A report always carries a name, so the rule's ``None`` is a refusal here
    rather than a fallback. And a report stores what was written, so a name the
    rule would have changed is a name no writer produced.
    """
    try:
        cleaned = clean_system_name(name)
    except ValueError:
        return (False, None)
    if cleaned is None or cleaned != name:
        return (False, None)
    return (True, cleaned)


@pytest.mark.parametrize("name", NAMES)
def test_the_stored_report_name_answers_to_the_rule(name: str) -> None:
    """The read-back field is the third door, and it reads the same rule.

    It stated the bound and the character rule itself and left the trim rule
    behind, which admitted ``"   "`` and ``"  Orders API  "`` — names the
    writer turns into "no system named" and ``"Orders API"``. Reverting it to a
    restatement fails here.

    Every name in the archive is plain ASCII with no padding and well inside
    the bound, so nothing stored is refused by this: the 308 artifact files
    carry 14 distinct names and the rule rewrites none of them.
    """
    assert _stored_outcome(name) == _stored_rule_outcome(name)


@pytest.mark.parametrize("name", ["   ", "  Orders API  "])
def test_a_stored_name_the_writer_could_not_have_written_is_refused(
    name: str,
) -> None:
    """The two shapes the old restatement let through, named one at a time.

    Refused rather than trimmed. A report validated into a ``system_name`` the
    file does not hold re-dumps to different bytes, so a read and a re-digest
    would break an ``attestation`` seal nobody edited.
    """
    with pytest.raises(ValidationError):
        InputRef.of(system_name=name, sources=[Source.description("a web app")])


@pytest.mark.parametrize("name", [BIDI_OVERRIDE, ZERO_WIDTH, NEXT_LINE])
def test_a_stored_report_name_is_read_by_the_same_rule(name: str) -> None:
    """A report is deserialized too, so its field refuses what the doors refuse."""
    with pytest.raises(ValidationError):
        InputRef.of(system_name=name, sources=[Source.description("a web app")])
