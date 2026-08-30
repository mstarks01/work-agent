"""The evidence-kind table, and the runs that corrected it.

The table is a judgement per requirement, so most of it cannot be tested. Two
things can: that it covers the catalog exactly, and that it does not contradict
what live runs already proved.
"""

from __future__ import annotations

import pytest

from analysis_service.frameworks.asvs.catalog import REQUIREMENTS
from analysis_service.frameworks.asvs.evidence_kinds import (
    EVIDENCE_KINDS,
    KINDS,
    settles,
    unsettled_by,
)

#: Requirements a live ASVS run ruled ``confirmed`` against a prose-only job,
#: over four sweeps on 2026-08-29.
#:
#: **A ``confirmed`` verdict means the Source settled the ruling** — this
#: package's critic contract defines it as "the requirement applies to this
#: system, and the input does not show it satisfied". So every one of these is
#: proof that prose can settle that requirement, and a table saying otherwise is
#: wrong about a case that already happened.
#:
#: This list caught four errors in the first pass. All four asked whether a
#: named control existed, and the rule in the module docstring — prose settles
#: "does X exist", not "is X right" — is what those four forced.
SETTLED_BY_PROSE_IN_A_RUN = (
    "V10.1.1",  # browser-held OAuth tokens reach components that do not need them
    "V12.2.1",  # the external-facing path is identified, and its transport with it
    "V13.3.1",  # the provider API key sits in the container environment
    "V14.2.1",  # a bearer token is placed in the progress-stream URL
    "V14.3.3",  # access and refresh tokens sit in browser local storage
    "V15.2.2",  # the fan-out has no stated availability defence
    "V2.3.2",  # business-logic limits are not implemented
    "V2.4.1",  # submissions carry no anti-automation control
    "V3.4.2",  # CORS reflects the request Origin
    "V6.1.1",  # authentication anti-automation controls are not documented
    "V6.3.1",  # credential-stuffing controls are explicitly absent
)


class TestTheTableCoversTheCatalog:
    def test_every_requirement_has_a_kind(self):
        assert set(EVIDENCE_KINDS) == {r.id for r in REQUIREMENTS}

    def test_every_entry_names_a_known_kind(self):
        for name, kinds in EVIDENCE_KINDS.items():
            assert kinds, name
            assert set(kinds) <= set(KINDS), name

    def test_the_import_check_is_not_vacuous(self):
        """Guards the guard: a table over an empty catalog agrees with anything."""
        assert len(EVIDENCE_KINDS) == len(REQUIREMENTS) == 345


class TestItDoesNotContradictTheRuns:
    @pytest.mark.parametrize("requirement", SETTLED_BY_PROSE_IN_A_RUN)
    def test_a_requirement_prose_already_settled_carries_prose(self, requirement):
        assert settles(requirement, ["prose"]), (
            f"{requirement} was ruled confirmed against a prose-only job, so a"
            " Source settled it. The table says prose cannot, which contradicts"
            " a run that already happened."
        )

    def test_the_ground_truth_list_names_real_requirements(self):
        assert set(SETTLED_BY_PROSE_IN_A_RUN) <= set(EVIDENCE_KINDS)


class TestSettles:
    def test_any_one_sufficient_kind_settles_it(self):
        assert settles("V12.2.1", ["prose"])
        assert settles("V12.2.1", ["config"])

    def test_a_job_carrying_none_of_them_does_not(self):
        assert not settles("V1.2.4", ["prose"])

    def test_a_richer_job_settles_a_superset(self):
        """The whole point of keying on the job rather than the requirement.

        Nothing is discounted permanently: a requirement code settles returns
        the day the service accepts code, with no edit to the table.
        """
        prose_only = set(unsettled_by(["prose"], level=1))
        with_code = set(unsettled_by(["prose", "code"], level=1))

        assert with_code < prose_only
        assert "V1.2.4" in prose_only
        assert "V1.2.4" not in with_code

    def test_an_unknown_requirement_raises_rather_than_answering(self):
        """A silent False would scope it out of every job."""
        with pytest.raises(KeyError):
            settles("V99.9.9", ["prose"])


def test_unsettled_is_bounded_by_level():
    """A level-1 job is never told about a level-2 requirement."""
    level_one = unsettled_by(["prose"], level=1)
    levels = {r.id: r.level for r in REQUIREMENTS}

    assert level_one
    assert all(levels[name] == 1 for name in level_one)
