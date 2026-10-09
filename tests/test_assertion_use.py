"""One job's use of its assertion catalog, read from its report."""

from __future__ import annotations

import argparse

from analysis_service.assertion_use import assertion_use
from analysis_service.assertions import (
    ABSENT,
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Subject,
    assertion_id,
)
from analysis_service.candidates import Candidate, CandidateSet, catalog_leads
from analysis_service.claims import Ground
from analysis_service.graph import ASSERT_NODE
from analysis_service.report import NodeRun
from evals.harness.assertion_use import command_assertion_use
from tests.factories import sample_report, sample_threat

SHOPPERS = Subject(
    id="principal:shopper-accounts", type="principal", label="shopper accounts"
)
ABSENCE = Assertion(
    subject=SHOPPERS.id,
    predicate="mfa-requirement",
    value=ABSENT,
    basis="inferred",
    explanation="the description names password login only",
)
ON_ROW = Ground(kind="assertion", assertion=assertion_id(ABSENCE))


def report(grounds):
    record = AssertionRecord(
        proposed=1, catalog=AssertionCatalog(subjects=[SHOPPERS], entries=[ABSENCE])
    )
    return sample_report(threats=[sample_threat(grounds=grounds)], assertions=record)


def test_a_job_that_ran_no_pass_has_no_figures():
    assert assertion_use(sample_report()) is None


def test_a_claim_on_a_catalog_row_alone_rests_only_on_the_catalog():
    (block,) = assertion_use(report([ON_ROW])).blocks

    assert (block.claims, block.citing, block.catalog_only) == (1, 1, 1)


def test_a_claim_with_a_quote_beside_the_row_only_cites_it():
    (block,) = assertion_use(report([*sample_threat().grounds, ON_ROW])).blocks

    assert (block.citing, block.catalog_only) == (1, 0)


def test_the_cost_is_the_assert_node_s_own():
    held = report([ON_ROW])
    held = held.model_copy(
        update={
            "nodes": [
                *held.nodes,
                NodeRun(node=ASSERT_NODE, duration_ms=1500, reported_charge_usd=0.02),
            ]
        }
    )
    use = assertion_use(held)

    assert (use.rows, use.assert_ms, use.assert_charge_usd) == (1, 1500, 0.02)


def test_a_report_with_no_catalog_leads_figure_reads_as_unmeasured():
    """A report from before ``prepare`` measured the leads is not a zero."""
    (block,) = assertion_use(report([ON_ROW])).blocks

    assert block.catalog_leads is None


def lanes(*leads):
    return {
        "spoofing": CandidateSet(
            lane="spoofing",
            candidates=tuple(
                Candidate(rule_id=rule, lane="spoofing", element_ids=elements)
                for rule, elements in leads
            ),
        )
    }


def test_a_catalog_lead_is_a_rule_on_elements_the_model_alone_does_not_fire():
    """One rule on one more element is one more lead."""
    held = lanes(("r-1", ("process:a",)), ("r-1", ("process:b",)), ("r-2", ("x",)))
    without = lanes(("r-1", ("process:a",)))

    assert catalog_leads(held, without) == 2


def test_the_command_reads_each_report_under_a_directory(tmp_path, capsys):
    (tmp_path / "01-case.report.json").write_text(report([ON_ROW]).model_dump_json())
    (tmp_path / "02-case.report.json").write_text(sample_report().model_dump_json())
    (tmp_path / "03-case.report.json").write_text("{}")

    assert command_assertion_use(argparse.Namespace(reports=[tmp_path])) == 0

    out = capsys.readouterr().out
    assert "| 01-case | stride | 1 | 1 | 1 | — | 1 |" in out
    assert (
        "Reports read: 1. Reports with no assertion pass: 1."
        " Reports today's model refuses: 1." in out
    )
