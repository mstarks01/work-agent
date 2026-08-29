"""The instrument that catches a justification which passes and says nothing.

Both fixtures below are the two real defects, reduced. Neither was visible to
any offline check when it shipped, because the suite scripts the agents: a
scripted pointer always resolves and a scripted quote always verifies, so every
check went green over two packages and eight months.
"""

from __future__ import annotations

from analysis_service.report import Ground, UnknownRef, Verdict
from evals.harness import filler
from tests.factories import sample_report, sample_threat


def _report(*threats):
    return sample_report(list(threats))


def _pointing_at(element_id: str, attribute: str):
    """A needs-info claim whose question hangs on one element attribute."""
    return sample_threat(
        verdict=Verdict(
            status="needs-info",
            reason="unsettled",
            related_unknowns=[UnknownRef(element_id=element_id, attribute=attribute)],
        )
    )


class TestIneligiblePointers:
    """The `notes` defect: a question pointed at a field that always resolves.

    ``notes`` exists on every element type, so it resolves for any element and
    discriminates between none. The evidence catalog already refuses it as a
    ground; nothing refused it as a question until this instrument counted it.
    """

    def test_a_type_specific_attribute_is_eligible(self):
        report = _report(_pointing_at("store:orders-db", "encryption_at_rest"))

        row = filler.rows([report])[0]

        assert row.pointers == 1
        assert row.ineligible == 0
        assert row.ineligible_share == 0.0

    def test_a_universal_attribute_is_not(self):
        report = _report(_pointing_at("store:orders-db", "notes"))

        row = filler.rows([report])[0]

        assert row.ineligible == 1
        assert row.ineligible_share == 1.0

    def test_the_subject_spelling_is_never_counted(self):
        """It names no attribute, so it has none the catalog could refuse.

        Counting it would score the fix for #411 as the defect it repaired.
        """
        report = _report(
            sample_threat(
                verdict=Verdict(
                    status="needs-info",
                    reason="unsettled",
                    related_unknowns=[
                        UnknownRef(subject="are database queries parameterized")
                    ],
                )
            )
        )

        row = filler.rows([report])[0]

        assert row.pointers == 0
        assert row.ineligible_share == 0.0

    def test_eligibility_follows_the_element_type(self):
        """`exposure` is real on a process and on nothing else.

        The critic in #409 named a real attribute of the wrong type, which is
        why this resolves against the model rather than against a name list.
        """
        report = _report(_pointing_at("entity:customer", "exposure"))

        row = filler.rows([report])[0]

        assert row.ineligible == 1


class TestGroundConcentration:
    """The arbitrary-quote defect, as a number rather than a judgement."""

    def test_one_kind_set_everywhere_reads_as_total_concentration(self):
        quote = Ground(kind="quote", text="Shoppers sign in", source_label="note")
        report = _report(
            sample_threat("S-01", grounds=[quote]),
            sample_threat("S-02", grounds=[quote]),
        )

        row = filler.rows([report])[0]

        assert row.modal_grounds == "quote"
        assert row.modal_share == 1.0

    def test_a_spread_reads_lower(self):
        quote = Ground(kind="quote", text="Shoppers sign in", source_label="note")
        unknown = Ground(
            kind="unknown-attribute", element_id="store:orders-db", attribute="protocol"
        )
        report = _report(
            sample_threat("S-01", grounds=[quote]),
            sample_threat("S-02", grounds=[quote, unknown]),
        )

        row = filler.rows([report])[0]

        assert row.modal_share == 0.5


def test_the_readings_are_per_framework():
    """Pooling two packages would hide either one behind the other's shape."""
    report = _report(_pointing_at("store:orders-db", "notes"))

    rows = filler.rows([report])

    assert [row.framework for row in rows] == ["stride"]
    assert rows[0].claims == 1


def test_an_empty_sweep_renders_and_writes_its_key(capsys):
    """A sweep that measured nothing must stay distinguishable from a lost one."""
    filler.render(filler.rows([]))

    assert "Nothing to read" in capsys.readouterr().out
    assert filler.artifact(filler.rows([])) == {"filler": {}}
