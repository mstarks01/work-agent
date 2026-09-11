"""The two digests a vote records: what it judged, in structure and in prose.

The fingerprint names the topic and is blind to what a claim says. These tests
hold the other half — that re-arguing a claim moves the structural digest, that
rewording alone does not, that a re-wrap moves neither, and that each table
answers for the shared judgement types a package may declare.
"""

from __future__ import annotations

from typing import get_args

import pytest

from analysis_service.claims import (
    FrameworkAnalysis,
    Ground,
    Mitigation,
    Severity,
    UnknownRef,
    Verdict,
)
from analysis_service.frameworks import PACKAGES
from evals.harness.content import (
    PROSE_FIELDS,
    PROSE_PREFIX,
    PROSE_VERSION,
    PROSE_VERSIONS,
    STRUCTURAL_FIELDS,
    STRUCTURAL_PREFIX,
    STRUCTURAL_VERSION,
    STRUCTURAL_VERSIONS,
    ContentError,
    prose,
    structural,
    version_of,
)
from tests.eval_factories import produced_threat
from tests.test_asvs import sample_asvs_claim

#: Separators a model emits that the page renders as a space.
SEPARATORS = tuple(chr(code) for code in (0x2028, 0x2029, 0x0085, 0x000B, 0x00A0))


#: Fixed, so a test that changes the title changes only the title. The factory
#: derives its description from the title, which would make every retitle a
#: prose change and hide what these tests are about.
DESCRIPTION = "The endpoint accepts a repeated request with no nonce."


def claim(title="An attacker replays a checkout request", **update):
    """One ruled threat, with a description the title does not compose."""
    threat = produced_threat(1, "spoofing", title)
    return threat.model_copy(update={"description": DESCRIPTION, **update})


def test_each_value_names_its_own_rule():
    """Two digests in one row, and no reader may confuse them."""
    threat = claim()

    assert version_of(structural(threat), STRUCTURAL_PREFIX) == STRUCTURAL_VERSION
    assert version_of(prose(threat), PROSE_PREFIX) == PROSE_VERSION


def test_a_prose_digest_is_refused_where_a_structural_one_belongs():
    """Swapped, it would read live against every claim."""
    with pytest.raises(ContentError, match="not a 's' digest"):
        version_of(prose(claim()), STRUCTURAL_PREFIX)


def test_a_fingerprint_is_not_a_digest():
    with pytest.raises(ContentError, match="not a 's' digest"):
        version_of("v6:0123456789abcdef", STRUCTURAL_PREFIX)


@pytest.mark.parametrize(
    ("compute", "known"),
    [(structural, STRUCTURAL_VERSIONS), (prose, PROSE_VERSIONS)],
    ids=["structural", "prose"],
)
def test_an_unknown_version_is_refused_rather_than_computed(compute, known):
    with pytest.raises(ContentError, match="not one this build computes"):
        compute(claim(), max(known) + 1)


class TestTheStructuralDigest:
    """What a substance vote judged: the verdict, the grounds, the ratings."""

    def test_a_different_verdict_moves_it(self):
        confirmed = claim()
        asked = confirmed.model_copy(
            update={
                "verdict": Verdict(
                    status="needs-info",
                    reason="authentication is unknown",
                    related_unknowns=[
                        UnknownRef(
                            element_id="entity:shopper", attribute="authentication"
                        )
                    ],
                )
            }
        )

        assert structural(confirmed) != structural(asked)

    def test_a_different_ground_moves_it(self):
        base = claim()
        elsewhere = base.model_copy(
            update={
                "grounds": [
                    Ground(
                        kind="unknown-attribute",
                        element_id="entity:customer",
                        attribute="authentication",
                    )
                ]
            }
        )

        assert structural(base) != structural(elsewhere)

    def test_a_different_rating_moves_it(self):
        base = claim()
        milder = base.model_copy(
            update={
                "severity": Severity(
                    likelihood="low", impact="high", level="medium", justification="j"
                )
            }
        )

        assert structural(base) != structural(milder)

    def test_a_requoted_span_does_not_move_it(self):
        """The quote's words are prose, and the grounding ladder already proves
        the span is present. A lane re-quoting a longer sentence is making the
        same argument."""
        base = claim()
        longer = base.model_copy(
            update={
                "grounds": [
                    ground.model_copy(update={"text": f"{ground.text} and more"})
                    if ground.text
                    else ground
                    for ground in base.grounds
                ]
            }
        )

        assert structural(base) == structural(longer)

    def test_a_rewording_does_not_move_it(self):
        """The whole reason the two digests are separate. Over the six case 01
        runs of one configuration a prose digest was shared by 0 of 234 pairs,
        so a substance vote bound to prose would expire on every sweep."""
        base = claim()
        reworded = base.model_copy(
            update={"description": "The same argument, said differently."}
        )

        assert structural(base) == structural(reworded)

    def test_a_retitle_does_not_move_it(self):
        assert structural(claim()) == structural(claim(title="Said another way"))


class TestTheProseDigest:
    """What a style vote judged: the description and the mitigations."""

    def test_a_rewritten_description_moves_it(self):
        assert prose(claim()) != prose(
            claim(description="All controls here are adequate.")
        )

    def test_a_rewritten_mitigation_moves_it(self):
        base = claim()
        advised = base.model_copy(
            update={"mitigations": [Mitigation(summary="Rotate keys", detail="Daily.")]}
        )

        assert prose(base) != prose(advised)

    def test_a_rewrapped_paragraph_does_not_move_it(self):
        """The page is HTML and collapses whitespace, so this changes nothing a
        reviewer can see."""
        assert prose(claim(description="one two three")) == prose(
            claim(description="one   two\n\tthree ")
        )

    @pytest.mark.parametrize(
        "separator",
        SEPARATORS,
        ids=["line-sep", "para-sep", "next-line", "vertical-tab", "no-break-space"],
    )
    def test_an_exotic_separator_collapses_too(self, separator):
        """A model emits more separators than a newline, and the page renders
        every one as a space. The rule asks the parser rather than the sample
        input: the Unicode whitespace class covers all of these."""
        assert prose(claim(description="one two")) == prose(
            claim(description=f"one{separator}two")
        )

    def test_a_retitle_does_not_move_it(self):
        """ADR 0029: the identity already carries what a title says, and a
        retitle for readability would expire every vote a sitting produced."""
        assert prose(claim()) == prose(claim(title="Said another way"))


class TestTheTablesAnswerForEveryPackage:
    """Read off the record's fields, never off a framework name."""

    def test_every_shared_judgement_type_has_a_row(self):
        """A shared type a record may declare and no table reads is a fact a
        vote would go on answering for after it moved."""
        declared = {
            name
            for record in _ruled_records()
            for name, field in record.model_fields.items()
            if _is_shared_judgement(field.annotation)
        }

        assert declared == set(STRUCTURAL_FIELDS) | (
            set(PROSE_FIELDS) - {"description"}
        )

    def test_no_row_names_a_field_no_record_carries(self):
        """The other direction: a table nobody compares to its registry fails
        as quietly as the branch it replaced."""
        carried = {name for record in _ruled_records() for name in record.model_fields}

        assert set(STRUCTURAL_FIELDS) | set(PROSE_FIELDS) <= carried

    def test_a_package_that_grades_nothing_digests_less(self):
        """ASVS rules applicability and grades no harm, so it declares no
        severity and contributes none."""
        ruling = sample_asvs_claim()

        assert "severity" not in type(ruling).model_fields
        assert structural(ruling).startswith(
            f"{STRUCTURAL_PREFIX}{STRUCTURAL_VERSION}:"
        )

    def test_both_packages_digest_under_one_rule(self):
        """Prose quality and a re-argument are nobody's framework's property."""
        assert prose(sample_asvs_claim()).startswith(f"{PROSE_PREFIX}{PROSE_VERSION}:")


def _ruled_records() -> tuple[type, ...]:
    """Each package's ruled claim type, read off the block it fills.

    Found rather than listed, so a package added tomorrow is checked with no
    edit here — the completeness rule the vendor registry applies, pointed at a
    record instead of at a table.
    """
    records = tuple(
        get_args(block.model_fields["claims"].annotation)[0]
        for block in FrameworkAnalysis.__subclasses__()
    )
    assert len(records) == len(PACKAGES), "a package declares no block, or two"
    return records


def _is_shared_judgement(annotation) -> bool:
    """Is this field one of the judgement types ``claims.py`` defines?"""
    text = str(annotation)
    return any(
        f"analysis_service.claims.{shared.__name__}" in text
        for shared in (Verdict, Severity, Mitigation)
    )
