"""Archived emissions re-scored under today's coordinates, and every loss named (#961 step 4).

The probes are the issue's own classes: a store written as a process, a flow
pointed at the wrong endpoint, an actor under a plural, a dropped element
beside an invented one, and a reference row worded twice. Each is built off a
real corpus case so the alignment it reads is the shipped one.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from analysis_service.assertions import (
    ABSENT,
    Assertion,
    AssertionCatalog,
    assertion_id,
    identity_parts,
)
from analysis_service.system_model import SystemModel, normalize_element_ids
from analysis_service.validation import parse_and_validate
from evals.harness import replay
from evals.harness.alignment import align
from evals.harness.archive import kind_of
from evals.harness.artifact import load_artifact
from evals.harness.bundle import (
    assertions_from_reports,
    extractions_from_reports,
    reports_dir,
    write_assertions,
    write_extractions,
)
from evals.harness.modes import AssertionResult, ExtractionResult
from evals.harness.reference import load_corpus
from evals.harness.run import main, replay_artifact
from tests.test_evals_alignment import case, renamed
from tests.test_evals_extraction_losses import CASE
from tests.test_evals_provenance import provenance, sampling  # noqa: F401
from tests.test_evals_stability import score, write_run

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "evals" / "corpus"
EMISSIONS = REPO_ROOT / "evals" / "emissions"


def fates(golden, model: SystemModel | None) -> dict[str, replay.ElementFate]:
    rows = replay.classify_elements(golden.model, model, align(golden, model))
    return {row.reference: row for row in rows}


def without(model: SystemModel, element_id: str) -> SystemModel:
    """The model with one element and every flow through it removed."""
    raw = model.model_dump(mode="json")
    for key in ("external_entities", "processes", "data_stores", "trust_boundaries"):
        raw[key] = [element for element in raw[key] if element["id"] != element_id]
    raw["data_flows"] = [
        flow
        for flow in raw["data_flows"]
        if flow["id"] != element_id
        and element_id not in (flow["source"], flow["destination"])
    ]
    return SystemModel.model_validate(raw)


class TestEveryBlessedElementTakesOneFate:
    @pytest.fixture(scope="class")
    def golden(self):
        return case("01")

    def test_the_blessed_model_against_itself_is_found_throughout(self, golden):
        rows = fates(golden, golden.model)

        assert {row.fate for row in rows.values()} == {"found"}
        assert len(rows) == len(list(golden.model.elements()))

    def test_no_model_leaves_every_element_omitted(self, golden):
        rows = fates(golden, None)

        assert {row.fate for row in rows.values()} == {"omitted"}

    def test_a_reader_ruled_alias_reads_as_renamed(self):
        """Case 09's spreadsheet under the name the reader ruled supported."""
        golden = case("09")
        model = renamed(golden.model, "process:catalogue-spreadsheet", "Spreadsheet")

        row = fates(golden, model)["process:catalogue-spreadsheet"]

        assert row.fate == "renamed"
        assert row.produced == "process:spreadsheet"

    def test_a_plural_reads_as_respelled_not_as_a_loss_to_rule_on(self, golden):
        model = renamed(golden.model, "entity:shopper", "Shoppers")

        row = fates(golden, model)["entity:shopper"]

        assert row.fate == "respelled"
        assert row.candidates == ("entity:shoppers",)

    def test_a_store_written_as_a_process_reads_as_mistyped(self, golden):
        copy = golden.model.model_copy(deep=True)
        store = next(s for s in copy.data_stores if s.id == "store:orders-db")
        copy.data_stores.remove(store)
        raw = copy.model_dump(mode="json")
        raw["processes"].append(
            {
                **next(p for p in raw["processes"]),
                "id": "process:orders-db",
                "name": store.name,
            }
        )
        model, _ = parse_and_validate(raw, normalize_ids=True)
        assert model is not None

        row = fates(golden, model)["store:orders-db"]

        assert row.fate == "mistyped"
        assert row.candidates == ("process:orders-db",)

    def test_a_dropped_element_with_no_type_mate_is_omitted(self, golden):
        model = without(golden.model, "store:receipt-archive")

        rows = fates(golden, model)

        assert rows["store:receipt-archive"].fate == "omitted"
        assert (
            rows["flow:order-service-to-receipt-archive:append-receipt"].fate
            == "endpoint_unaligned"
        )

    def test_a_dropped_element_beside_an_invented_type_mate_is_unresolved(self, golden):
        """One missing store and one extra store: a rename nobody ruled, or not."""
        model = renamed(golden.model, "store:receipt-archive", "Ledger Vault")

        row = fates(golden, model)["store:receipt-archive"]

        assert row.fate == "unresolved"
        assert row.candidates == ("store:ledger-vault",)

    def test_a_flow_pointed_at_another_endpoint_is_misattached(self, golden):
        copy = golden.model.model_copy(deep=True)
        flow = next(
            f
            for f in copy.data_flows
            if f.id == "flow:order-service-to-receipt-archive:append-receipt"
        )
        flow.source = "process:storefront-api"
        model = normalize_element_ids(copy)

        row = fates(golden, model)[
            "flow:order-service-to-receipt-archive:append-receipt"
        ]

        assert row.fate == "misattached"
        assert row.candidates == (
            "flow:storefront-api-to-receipt-archive:append-receipt",
        )

    def test_a_flow_between_found_endpoints_under_another_label_is_unresolved(
        self, golden
    ):
        copy = golden.model.model_copy(deep=True)
        flow = next(
            f
            for f in copy.data_flows
            if f.id == "flow:order-service-to-receipt-archive:append-receipt"
        )
        flow.name = "Nightly export"
        flow.operations = "read"
        model = normalize_element_ids(copy)

        row = fates(golden, model)[
            "flow:order-service-to-receipt-archive:append-receipt"
        ]

        assert row.fate == "unresolved"
        assert row.candidates == (
            "flow:order-service-to-receipt-archive:nightly-export",
        )

    def test_a_flow_dropped_between_found_endpoints_is_omitted(self, golden):
        copy = golden.model.model_copy(deep=True)
        copy.data_flows[:] = [
            f
            for f in copy.data_flows
            if f.id != "flow:order-service-to-receipt-archive:append-receipt"
        ]

        row = fates(golden, copy)[
            "flow:order-service-to-receipt-archive:append-receipt"
        ]

        assert row.fate == "omitted"

    def test_a_wrong_fact_is_an_attribute_on_a_paired_element(self, golden):
        copy = golden.model.model_copy(deep=True)
        next(
            p for p in copy.processes if p.id == "process:order-service"
        ).exposure = "internet-facing"
        result = ExtractionResult(golden.id, copy, ())

        graded = replay.replay_extraction(golden, result)

        assert [c.key for c in graded.wrong_facts] == ["process.exposure"]
        assert graded.counts["found"] == len(list(golden.model.elements()))


class TestEveryReferenceRowTakesOneFate:
    @pytest.fixture(scope="class")
    def golden(self):
        return case("01")

    @pytest.fixture(scope="class")
    def reference(self, golden):
        catalog = replay.signed_reference(CORPUS, golden)
        if catalog is None:
            pytest.skip("case 01's facts are unsigned, so nothing grades a proposal")
        return catalog

    def graded(self, golden, reference, entries, issues=()):
        produced = AssertionCatalog(subjects=reference.subjects, entries=list(entries))
        result = AssertionResult(golden.id, {"assertions": []}, produced, tuple(issues))
        return replay.replay_assertions(golden, reference, result)

    def test_the_reference_against_itself_is_found_throughout(self, golden, reference):
        graded = self.graded(golden, reference, reference.entries)

        assert {row.fate for row in graded.rows} == {"found"}
        assert set(graded.produced.values()) == {"matched"}

    def test_a_reworded_text_value_is_a_readers_question(self, golden, reference):
        row = next(e for e in reference.entries if e.predicate == "storage-encryption")
        reworded = row.model_copy(update={"value": "a key the customer manages"})
        entries = [reworded if e is row else e for e in reference.entries]

        graded = self.graded(golden, reference, entries)

        assert graded.counts["worded"] == 1
        assert graded.counts["found"] == len(reference.entries) - 1

    def test_a_reworded_term_is_a_wrong_value(self, golden, reference):
        row = next(
            e
            for e in reference.entries
            if e.predicate == "internet-exposure" and e.value == "internal"
        )
        flipped = row.model_copy(update={"value": "internet-facing"})
        entries = [flipped if e is row else e for e in reference.entries]

        graded = self.graded(golden, reference, entries)

        assert graded.counts["wrong_value"] == 1

    def test_a_stated_fact_answered_as_absent_is_a_wrong_value(self, golden, reference):
        row = next(e for e in reference.entries if e.predicate == "storage-encryption")
        denied = row.model_copy(update={"value": ABSENT})
        entries = [denied if e is row else e for e in reference.entries]

        graded = self.graded(golden, reference, entries)

        assert graded.counts["wrong_value"] == 1

    def test_a_signed_subject_alias_pairs_a_row_named_otherwise(
        self, golden, reference
    ):
        """The reviewer rules two spellings name one principal; the matcher reads it."""
        row = next(
            e
            for e in reference.entries
            if e.predicate == "authorization-grant"
            and e.subject == "principal:application-account"
        )
        renamed = row.model_copy(
            update={"subject": "principal:single-application-account"}
        )
        unsigned = replay.SignedReference(reference.catalog)
        signed = replay.SignedReference(
            reference.catalog,
            subject_aliases={
                "principal:single-application-account": replay.AliasTarget(row.subject)
            },
        )

        before = self.graded(golden, unsigned, [renamed])
        after = self.graded(golden, signed, [renamed])

        # Unsigned, the row sits on a subject nobody ruled on: its grant is one
        # the reference carries elsewhere, so it reads as a misattachment.
        assert before.produced == {assertion_id(renamed): "misattached"}
        assert after.produced == {assertion_id(row): "matched"}
        assert next(r.fate for r in after.rows if r.reference == assertion_id(row)) == (
            "found"
        )

    def test_a_subject_alias_also_rewrites_a_value_that_points_at_it(
        self, golden, reference
    ):
        """A credential named otherwise is one credential wherever a row points at it."""
        row = next(
            e
            for e in reference.entries
            if e.predicate == "credential-presented"
            and e.value == "credential:application-account-password"
        )
        pointed = row.model_copy(update={"value": "credential:password"})
        signed = replay.SignedReference(
            reference.catalog,
            subject_aliases={"credential:password": replay.AliasTarget(row.value)},
        )

        graded = self.graded(golden, signed, [pointed])

        assert next(
            r.fate for r in graded.rows if r.reference == assertion_id(row)
        ) == ("found")

    def test_a_value_alias_holds_only_under_the_subjects_the_ruling_names(
        self, golden, reference
    ):
        """ "Password" is the application account's within its rows, not a shopper's."""
        row = next(
            e
            for e in reference.entries
            if e.predicate == "credential-presented"
            and e.value == "credential:application-account-password"
        )
        bounded = replay.AliasTarget(row.value, frozenset({row.subject}))
        signed = replay.SignedReference(
            reference.catalog, subject_aliases={"credential:password": bounded}
        )
        theirs = row.model_copy(update={"value": "credential:password"})
        shoppers = theirs.model_copy(update={"subject": "principal:shopper-accounts"})

        assert replay.under_aliases(theirs, signed).value == row.value
        assert replay.under_aliases(shoppers, signed).value == "credential:password"

    def test_a_signed_qualifier_alias_turns_a_rescoped_row_into_a_found_one(
        self, golden, reference
    ):
        row = next(
            e
            for e in reference.entries
            if e.predicate == "authorization-grant"
            and e.subject == "principal:application-account"
        )
        respelled = row.model_copy(
            update={
                "scope": [
                    q.model_copy(update={"value": "read and write"})
                    if q.kind == "operation"
                    else q
                    for q in row.scope
                ]
            }
        )
        signed = replay.SignedReference(
            reference.catalog,
            qualifier_aliases={("operation", "read and write"): "read-write"},
        )

        before = self.graded(
            golden, replay.SignedReference(reference.catalog), [respelled]
        )
        after = self.graded(golden, signed, [respelled])

        fate = lambda g: next(
            r.fate for r in g.rows if r.reference == assertion_id(row)
        )
        assert fate(before) == "rescoped"
        assert fate(after) == "found"

    def test_a_silent_unknown_nobody_produced_is_silent_not_omitted(
        self, golden, reference
    ):
        """The prompt forbids the row and the reference records the forced placement."""
        graded = self.graded(golden, reference, [])

        silent = {r.reference for r in graded.rows if r.fate == "silent"}
        expected = {
            assertion_id(e)
            for e in reference.entries
            if e.value == "unknown" and e.reason == "silent"
        }
        assert silent == expected
        assert expected, "case 01 carries silent placements under the step-3 rulings"
        assert all(
            r.fate == "omitted" for r in graded.rows if r.reference not in expected
        )

    def test_a_value_produced_against_a_silent_unknown_is_wrong(
        self, golden, reference
    ):
        row = next(
            e
            for e in reference.entries
            if e.value == "unknown" and e.reason == "silent"
        )
        placed = row.model_copy(
            update={"value": "boundary:core-services", "reason": None}
        )

        graded = self.graded(golden, reference, [placed])

        assert next(
            r.fate for r in graded.rows if r.reference == assertion_id(row)
        ) == ("wrong_value")

    def test_an_unsigned_alias_rewrites_nothing(self, tmp_path):
        """A draft alias is a draft: the signed reference carries only signed ones."""
        golden = case("01")
        source = CORPUS / golden.id
        target = tmp_path / golden.id
        target.mkdir()
        for path in source.iterdir():
            if path.is_file():
                (target / path.name).write_bytes(path.read_bytes())
        facts = json.loads((target / "facts.json").read_text("utf-8"))
        facts["aliases"] = {
            "subjects": [
                {
                    "subject": "principal:application-account",
                    "names": ["single application account"],
                    "ruling": "drafted",
                    "reviewed_by": None,
                }
            ],
            "qualifiers": [
                {
                    "kind": "operation",
                    "value": "read-write",
                    "names": ["read and write"],
                    "ruling": "signed",
                    "reviewed_by": "someone",
                }
            ],
        }
        (target / "facts.json").write_text(json.dumps(facts), "utf-8")

        signed = replay.signed_reference(tmp_path, golden)

        assert signed is not None
        assert signed.subject_aliases == {}
        assert signed.qualifier_aliases == {
            ("operation", "read and write"): "read-write"
        }

    def test_a_dropped_row_is_omitted_and_a_new_row_is_unreviewed(
        self, golden, reference
    ):
        row = reference.entries[0]
        extra = Assertion(
            subject=row.subject,
            predicate="storage-encryption",
            value="nothing at rest",
            basis="stated",
        )
        entries = [*reference.entries[1:], extra]

        graded = self.graded(golden, reference, entries)

        assert graded.counts["omitted"] == 1
        assert graded.produced[assertion_id(extra)] == "unreviewed"

    def test_a_credential_on_the_flow_instead_of_its_principal_is_misattached(
        self, golden, reference
    ):
        """The convention the reference follows, read as a countable target."""
        row = next(
            e
            for e in reference.entries
            if e.predicate == "credential-presented"
            and e.subject == "principal:shopper-accounts"
        )
        moved = row.model_copy(
            update={"subject": "flow:shopper-to-storefront-api:place-order"}
        )
        entries = [moved if e is row else e for e in reference.entries]

        graded = self.graded(golden, reference, entries)

        assert graded.counts["omitted"] == 1
        assert graded.produced[assertion_id(moved)] == "misattached"

    def test_an_inferred_fact_claimed_as_stated_is_counted(self, golden, reference):
        row = next(e for e in reference.entries if e.basis == "inferred")
        overstated = row.model_copy(update={"basis": "stated"})
        entries = [overstated if e is row else e for e in reference.entries]

        graded = self.graded(golden, reference, entries)

        assert sum(r.basis_overstated for r in graded.rows) == 1

    def test_a_rejected_row_is_counted_by_row_not_by_issue(self, golden, reference):
        from analysis_service.assertions import CatalogIssue

        issues = [
            CatalogIssue(code="illegal-value", message="x", row=3),
            CatalogIssue(code="missing-scope", message="y", row=3),
        ]

        graded = self.graded(golden, reference, reference.entries, issues)

        assert graded.rejected == 1

    def test_identity_parts_compose_the_id(self, reference):
        for entry in reference.entries:
            parts = identity_parts(entry)
            assert assertion_id(entry) == (
                f"assertion:{parts.predicate}~{parts.subject}~{parts.scope}~{parts.value}"
            )


class TestAnUnsignedReferenceGradesNothing:
    def test_a_case_without_a_facts_file_is_none(self):
        golden = case("02")

        assert replay.unsigned_rows(CORPUS, golden) is None
        assert replay.signed_reference(CORPUS, golden) is None

    def test_an_unsigned_row_withholds_the_catalog(self, tmp_path):
        golden = case("01")
        source = CORPUS / golden.id
        target = tmp_path / golden.id
        target.mkdir()
        for path in source.iterdir():
            if path.is_file():
                (target / path.name).write_bytes(path.read_bytes())
        facts = json.loads((target / "facts.json").read_text("utf-8"))
        facts["rows"][0]["reviewed_by"] = None
        (target / "facts.json").write_text(json.dumps(facts), "utf-8")

        assert replay.unsigned_rows(tmp_path, golden) == 1
        assert replay.signed_reference(tmp_path, golden) is None


class TestTheBundleReadsBackWhatItWrote:
    @pytest.fixture(scope="class")
    def golden(self):
        return case("01")

    def test_an_extraction_re_parses_to_the_model_the_sweep_scored(
        self, golden, tmp_path
    ):
        raw = golden.model.model_dump(mode="json")
        sources = {source.label: source.text for source in golden.sources}
        model, issues = parse_and_validate(raw, normalize_ids=True, sources=sources)
        out = tmp_path / "sweep.json"
        write_extractions(
            str(out),
            "extraction",
            {golden.id: ExtractionResult(golden.id, model, tuple(issues), raw=raw)},
        )

        (read,) = extractions_from_reports(out, [golden]).values()

        assert read.raw == raw
        assert read.extracted == model
        assert read.issues == tuple(issues)
        assert read.repair is None

    def test_an_end_to_end_emission_reads_back_with_its_repair(self, golden, tmp_path):
        broken = golden.model.model_dump(mode="json")
        broken["data_flows"][0]["destination"] = "process:does-not-exist"
        repaired = golden.model.model_dump(mode="json")
        out = tmp_path / "sweep.json"
        write_extractions(
            str(out),
            "end-to-end",
            {
                golden.id: ExtractionResult(
                    golden.id, None, (), raw=broken, repair=repaired
                )
            },
        )

        (read,) = extractions_from_reports(out, [golden]).values()

        assert read.raw == broken
        assert read.repair == repaired
        assert [issue.code for issue in read.issues] == ["invalid-reference"]

    def test_a_proposal_re_resolves_to_the_catalog_the_sweep_counted(
        self, golden, tmp_path
    ):
        proposal = {
            "assertions": [
                {
                    "subject_type": "component",
                    "subject": "process:storefront-api",
                    "predicate": "internet-exposure",
                    "value": "internet-facing",
                    "scope": [],
                    "basis": "stated",
                    "quotes": [
                        {
                            "source_label": golden.sources[0].label,
                            "quote": "It is the only thing we expose to the internet.",
                        }
                    ],
                    "explanation": "",
                    "exclusive": False,
                }
            ]
        }
        out = tmp_path / "sweep.json"
        catalog = AssertionCatalog()
        write_assertions(
            str(out),
            "assertions",
            {golden.id: AssertionResult(golden.id, proposal, catalog, ())},
        )

        (read,) = assertions_from_reports(out, [golden]).values()

        assert read.proposal == proposal
        assert [e.predicate for e in read.catalog.entries] == ["internet-exposure"]

    def test_a_missing_bundle_is_refused_by_name(self, golden, tmp_path):
        from evals.harness.modes import EvalRunError

        with pytest.raises(EvalRunError, match="does not exist"):
            extractions_from_reports(tmp_path / "none.json", [golden])


#: The artifacts alone. An emission file beside one is named by a suffix the
#: archive declares, and an artifact is the one JSON file no suffix names.
ARTIFACTS = sorted(p for p in EMISSIONS.rglob("*.json") if kind_of(p.name) is None)


class TestTheArchivedEmissionsReplay:
    """The imported archive: every artifact loads, sits on one arm, and replays."""

    @pytest.fixture(scope="class")
    def corpus(self):
        return load_corpus(CORPUS)

    def test_the_archive_is_not_empty(self):
        assert len(ARTIFACTS) >= 21

    @pytest.mark.parametrize("path", ARTIFACTS, ids=[p.stem for p in ARTIFACTS])
    def test_every_artifact_carries_its_emissions_beside_it(self, path):
        loaded = load_artifact(path)
        directory = reports_dir(path)
        files = sorted(directory.iterdir())

        assert files, f"{directory} holds no emission"
        assert {kind_of(f.name) for f in files} == {
            "extraction" if loaded.mode == "extraction" else "assertions"
        }
        assert {f.name.split(".")[0] for f in files} <= set(loaded.cases)
        replay.arm_of(loaded)

    def test_every_extraction_emission_replays(self, corpus):
        sweeps = [
            replay_artifact(path, corpus, CORPUS)
            for path in ARTIFACTS
            if load_artifact(path).mode == "extraction"
        ]
        pool = replay.pooled_extraction(sweeps)

        assert pool["emissions"] == sum(
            len(list(reports_dir(p).iterdir()))
            for p in ARTIFACTS
            if load_artifact(p).mode == "extraction"
        )
        assert sum(pool["fates"].values()) == sum(
            len(list(next(c for c in corpus if c.id == r.case_id).model.elements()))
            for s in sweeps
            for r in s.extractions
        )
        assert pool["by_type"]["flow"]["endpoint_unaligned"] > 0

    def test_an_arm_is_the_model_that_answered_the_node_not_the_tier_table(
        self, tmp_path
    ):
        """Two sweeps on one tier table are two arms when the node ran on two tiers."""
        source = EMISSIONS / "20260914T201030Z-assert-model-benchmark" / "luna-r1.json"
        luna = replay.arm_of(load_artifact(source))
        raw = json.loads(source.read_text("utf-8"))
        terra_model = "openrouter/openai/gpt-5.6-terra"
        for execution in raw["provenance"]["node_runs"]["assert"]:
            execution["tier"] = "strong"
            execution["requested_model"] = terra_model
            execution["served_model"] = terra_model
        # The fingerprint the loader recomputes for that route and tier, so the
        # edited copy is a consistent artifact rather than a refused one.
        terra_fingerprint = (
            "16dcff0b2bbb77672bdf209bd81bee3a2a4b78dd73b680ee1df2ada91013363f"
        )
        for execution in raw["provenance"]["node_runs"]["assert"]:
            execution["generation_fingerprint"] = terra_fingerprint
        identities = raw["provenance"]["generation_identities"]
        identities["strong"] = identities.pop("base") | {
            "requested_models": [terra_model],
            "served_models": [terra_model],
            "fingerprints": [terra_fingerprint],
        }
        moved = tmp_path / "terra-r1.json"
        moved.write_text(json.dumps(raw), "utf-8")

        terra = replay.arm_of(load_artifact(moved))

        assert luna.models == ("openrouter/openai/gpt-5.6-luna",)
        assert terra.models == ("openrouter/openai/gpt-5.6-terra",)
        assert terra.instruction == luna.instruction
        assert terra != luna

    def test_the_spread_is_read_over_the_sweeps_that_ran_every_case(self, corpus):
        """A ceiling is priced against the sd of the fate's per-sweep count."""
        sweeps = [
            replay_artifact(path, corpus, CORPUS)
            for path in ARTIFACTS
            if load_artifact(path).mode == "extraction"
        ]
        by_arm = replay.by_arm(sweeps)
        before = next(
            arm
            for arm in by_arm
            if arm.instruction.startswith("88f0740b")
            and any("luna" in model for model in arm.models)
        )

        spread = replay.pooled_extraction(by_arm[before])["spread"]

        # Six sweeps on the arm; the preflight ran one case and two ran eleven.
        assert spread["sweeps"] == 3
        assert spread["sd"]["found"] == 7.23
        assert spread["by_type"]["entity"]["omitted"] == 0.0

    def test_one_sweep_has_no_spread(self, corpus):
        (sweep,) = [
            replay_artifact(path, corpus, CORPUS)
            for path in ARTIFACTS
            if path.name.endswith("preflight-01.json")
        ]

        spread = replay.pooled_extraction([sweep])["spread"]

        assert spread == {
            "sweeps": 1,
            "sd": dict.fromkeys(replay.FATES),
            "by_type": {
                kind: dict.fromkeys(replay.FATES)
                for kind in ("boundary", "entity", "flow", "process", "store")
            },
        }

    def test_the_command_runs_over_the_archive(self, tmp_path, capsys):
        out = tmp_path / "replay.json"
        code = main(["replay", *map(str, ARTIFACTS[:2]), "--out", str(out)])

        assert code == 0
        printed = capsys.readouterr().out
        assert "targets" in printed
        written = json.loads(out.read_text("utf-8"))
        assert set(written) == {"coordinates", "arms", "sweeps"}
        assert len(written["sweeps"]) == 2

    def test_a_sweep_of_another_mode_is_refused_by_name(
        self,
        corpus,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """An analysis sweep keeps its model inside its report, which this does not read."""
        from evals.harness.modes import EvalRunError

        path = write_run(
            tmp_path, "analysis.json", provenance(sampling), [score(CASE, 4, [0])]
        )

        with pytest.raises(EvalRunError, match="analysis sweep keeps no emission"):
            replay_artifact(path, corpus, CORPUS)

    def test_an_end_to_end_sweep_replays_its_first_pass(
        self,
        corpus,
        tmp_path,
        sampling,  # noqa: F811
    ):
        """The paired runs #961 step 6 asks for are end-to-end, and they grade here."""
        golden = next(c for c in corpus if c.id == CASE)
        path = write_run(
            tmp_path,
            "e2e.json",
            provenance(sampling),
            [score(CASE, 4, [0])],
            mode="end-to-end",
            instruction=[
                {
                    "framework": "(shared)",
                    "node": "extract",
                    "tokens": 1,
                    "sha256": "a" * 64,
                }
            ],
            models={
                "tiers_config_version": 9,
                "tiers": {
                    "base": {"vendor": "openrouter", "model": "x", "upstreams": []}
                },
            },
        )
        raw = golden.model.model_dump(mode="json")
        write_extractions(
            str(path),
            "end-to-end",
            {golden.id: ExtractionResult(golden.id, None, (), raw=raw)},
        )

        sweep = replay_artifact(path, corpus, CORPUS)

        (graded,) = sweep.extractions
        assert graded.counts["found"] == len(list(golden.model.elements()))

    def test_a_case_nobody_signed_is_skipped_by_name(self, corpus, tmp_path):
        bench = next(p for p in ARTIFACTS if "luna-r1" in p.name)
        old = tmp_path / "corpus"
        shutil.copytree(CORPUS, old, ignore=shutil.ignore_patterns("facts.json"))

        sweep = replay_artifact(bench, load_corpus(old), old)

        assert sweep.assertions == ()
        assert sweep.skipped == {
            "01-payments-checkout": "no facts file, so nothing grades it"
        }


class TestBindingAProposalToAnExtractedGraph:
    """Finding 9: fact omission and binding failure, counted apart."""

    @pytest.fixture(scope="class")
    def golden(self):
        return case("01")

    @pytest.fixture(scope="class")
    def proposal(self, golden):
        bench = next(p for p in ARTIFACTS if "luna-r1" in p.name)
        return assertions_from_reports(bench, [golden])[golden.id]

    def bound(self, golden, proposal, model, graph="graph"):
        return replay.bind_assertions(
            golden, proposal, ExtractionResult(golden.id, model, ()), graph
        )

    def test_the_blessed_graph_binds_every_row_the_blessed_resolver_kept(
        self, golden, proposal
    ):
        binding = self.bound(golden, proposal, golden.model)

        assert binding.parsed
        assert not any(binding.counts[fate] for fate in replay.BIND_LOSSES)
        assert binding.counts["bound"] + binding.counts["own"] > 0
        assert binding.counts["refused"] == len(
            {issue.row for issue in proposal.issues if issue.row is not None}
        )
        assert binding.offered == binding.offered_blessed

    def test_a_relabelled_flow_is_a_binding_the_alignment_would_take(
        self, golden, proposal
    ):
        """The flow keeps its endpoints and its discriminators under another
        label, so the resolver's snap misses it and the alignment pairs it."""
        flow = "flow:order-service-to-orders-db:read-write-orders"
        assert any(row["subject"] == flow for row in proposal.proposal["assertions"])
        model = renamed(golden.model, flow, "database access")
        binding = self.bound(golden, proposal, model)

        renamed_rows = [row for row in binding.rows if row.fate == "renamed"]
        assert {row.subject for row in renamed_rows} == {flow}
        assert {row.aligned for row in renamed_rows} == {
            "flow:order-service-to-orders-db:database-access"
        }
        assert binding.counts["omitted"] == 0

    def test_a_referent_the_graph_names_otherwise_is_charged_to_the_value(
        self, golden, proposal
    ):
        """A zone a component sits in is a reference value, and the alias
        ruling pairs the blessed zone with the produced one."""
        zone_rows = [
            row
            for row in proposal.proposal["assertions"]
            if row["predicate"] == "network-membership"
        ]
        assert zone_rows, "the fixture proposal places a component in a zone"
        model = renamed(golden.model, "boundary:core-services", "core network")
        binding = self.bound(golden, proposal, model)

        referent_rows = [row for row in binding.rows if row.fate == "referent_renamed"]
        assert referent_rows
        assert {row.aligned for row in referent_rows} == {"boundary:core-network"}
        assert all(row.predicate == "network-membership" for row in referent_rows)

    def test_a_dropped_element_is_an_omission_of_the_graph(self, golden, proposal):
        model = without(golden.model, "process:storefront-api")
        binding = self.bound(golden, proposal, model)

        omitted = [row for row in binding.rows if row.fate == "omitted"]
        assert omitted
        assert all(not row.aligned for row in omitted)

    def test_an_emission_that_did_not_parse_binds_nothing(self, golden, proposal):
        binding = self.bound(golden, proposal, None)

        assert not binding.parsed
        assert binding.rows == ()
        assert binding.offered == 0
        assert binding.offered_blessed > 0

    def test_the_archive_binds_and_the_command_runs(self, tmp_path, capsys):
        proposals = [p for p in ARTIFACTS if "luna-r1" in p.name]
        graphs = [p for p in ARTIFACTS if "preflight-01" in p.name]
        out = tmp_path / "bind.json"

        code = main(
            [
                "bind",
                *map(str, proposals),
                "--graphs",
                *map(str, graphs),
                "--out",
                str(out),
            ]
        )

        assert code == 0
        assert "bound" in capsys.readouterr().out
        written = json.loads(out.read_text("utf-8"))
        assert set(written) == {"coordinates", "arms", "bindings"}
        assert written["bindings"]
