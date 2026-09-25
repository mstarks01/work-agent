"""``ReferenceThreat`` and the corpus loader.

The loader is checked against the shipped corpus, not a synthetic fixture:
fitting the real corpus layout is the requirement, and only the real files test
it. Fail-closed behaviour gets its own tempdir cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_service.claims import derive_severity_level
from analysis_service.frameworks.stride.record import STRIDE_CATEGORIES
from analysis_service.system_model import SystemModel, normalize_element_ids
from evals import verify_corpus
from evals.harness import envelope as envelopes
from evals.harness import sitting as sittings
from evals.harness.identity import endpoint_form
from evals.harness.reference import (
    MAX_CORPUS_SOURCE_BYTES,
    RETIRED_FIELDS,
    CorpusError,
    ReferenceThreat,
    flows_by_case,
    load_case,
    load_corpus,
)
from evals.harness.sitting import moved

CORPUS_DIR = Path(__file__).resolve().parents[1] / "evals" / "corpus"


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(CORPUS_DIR)


def test_loads_every_shipped_case(corpus):
    assert len(corpus) == 13
    assert [case.id for case in corpus] == sorted(case.id for case in corpus)


# One near control per exemplar system, and the pairing is definitional rather
# than a judgement about resemblance: each control's domain is the domain its
# exemplar system was written in. `docs/adr/0006-two-exemplar-systems.md` has
# the reasoning; the short version is that "near" means "an architecture the
# exemplars actually demonstrate", so adding an exemplar system without adding
# its control leaves the delta measuring the wrong thing.
NEAR_EXEMPLAR_CONTROLS = ["01-payments-checkout", "02-iot-fleet-telemetry"]


def test_one_near_exemplar_control_per_exemplar_system(corpus):
    # Without a control there is nothing to subtract from, and the
    # exemplar-domain-bias delta is unmeasurable.
    near = [
        case.id
        for case in corpus
        if case.declaration("stride").exemplar_proximity == "near"
    ]
    assert near == NEAR_EXEMPLAR_CONTROLS


def test_the_near_controls_are_outnumbered_by_far_cases(corpus):
    """The delta compares two populations, so neither may be most of the corpus.

    A guard on the instrument rather than on the corpus: `far_recall` is what
    the honest question is asked of, and each near control taken out of the far
    population costs it a case. At two of thirteen that is comfortable; the check
    exists so a third exemplar system cannot quietly make it not.
    """
    near = sum(
        1 for case in corpus if case.declaration("stride").exemplar_proximity == "near"
    )
    assert near * 2 < len(corpus) - near


def test_every_case_carries_must_find_references(corpus):
    for case in corpus:
        must_find = case.must_find_for("stride")
        assert must_find, f"{case.id} would make tier 2 recall vacuous"
        assert len(case.claims_for("stride")) >= len(must_find)


def test_references_span_every_lane(corpus):
    for case in corpus:
        lanes = {reference.category for reference in case.claims_for("stride")}
        assert lanes == set(STRIDE_CATEGORIES), f"{case.id} misses lanes"


def test_reference_severity_band_uses_shipped_arithmetic(corpus):
    for case in corpus:
        for reference in case.claims_for("stride"):
            assert reference.severity.level == derive_severity_level(
                reference.severity.likelihood, reference.severity.impact
            )


def test_blessed_models_are_small_enough_to_enumerate(corpus):
    """Ground truth is only exhaustively enumerable by a person on small systems.

    The band comes from ``verify_corpus`` rather than being spelled again here.
    Two readers of one rule disagree eventually, and a ceiling written in both
    places is the shape that lets them: move one and the corpus is legal to one
    reader and illegal to the other, with each reader's own test agreeing with
    it.
    """
    for case in corpus:
        assert (
            verify_corpus.MIN_ELEMENTS
            <= len(case.model.elements())
            <= verify_corpus.MAX_ELEMENTS
        )


def test_reference_threat_rejects_unknown_fields():
    with pytest.raises(ValueError):
        ReferenceThreat.model_validate(
            {
                "category": "spoofing",
                "affected_element_ids": ["entity:x"],
                "claim": "An attacker does a thing.",
                "tier": "must-find",
                "severity": {"likelihood": "high", "impact": "high"},
                "mitigations": [],  # a DraftThreat field, never graded here
            }
        )


def test_reference_threat_rejects_unknown_tier():
    with pytest.raises(ValueError):
        ReferenceThreat.model_validate(
            {
                "category": "spoofing",
                "affected_element_ids": ["entity:x"],
                "claim": "An attacker does a thing.",
                "tier": "nice-to-have",
                "severity": {"likelihood": "high", "impact": "high"},
            }
        )


def _copy_case(source: Path, destination: Path) -> Path:
    case_dir = destination / source.name
    case_dir.mkdir(parents=True)
    for name in ("source.md", "model.json", "case.json"):
        (case_dir / name).write_bytes((source / name).read_bytes())
    claims = case_dir / "claims"
    claims.mkdir()
    for path in (source / "claims").glob("*.json"):
        (claims / path.name).write_bytes(path.read_bytes())
    return case_dir


def test_dangling_element_reference_fails_closed(tmp_path):
    # Mirrors the exemplar lint: a reference citing an element the blessed
    # model lacks is unscoreable, and dropping it silently would lower the
    # recall denominator without anyone noticing.
    case_dir = _copy_case(CORPUS_DIR / "01-payments-checkout", tmp_path)
    claims_file = case_dir / "claims" / "stride.json"
    threats = json.loads(claims_file.read_text())
    threats[0]["affected_element_ids"] = ["process:does-not-exist"]
    claims_file.write_text(json.dumps(threats))

    with pytest.raises(CorpusError, match="absent from model.json"):
        load_case(case_dir)


def test_invalid_blessed_model_fails_closed(tmp_path):
    case_dir = _copy_case(CORPUS_DIR / "02-iot-fleet-telemetry", tmp_path)
    model = json.loads((case_dir / "model.json").read_text())
    model["processes"][0]["trust_zone"] = "boundary:not-declared"
    (case_dir / "model.json").write_text(json.dumps(model))

    with pytest.raises(CorpusError, match="model.json is not valid"):
        load_case(case_dir)


def test_a_source_symlinked_out_of_the_case_is_refused_unread(tmp_path):
    """A stranger's corpus PR cannot make CI read a file out of the tree.

    `_load_sources` reads over an untrusted pull request tree in CI, so a
    `source.md` symlinked at `/proc/self/pagemap` -- or any path outside the
    case -- must be refused rather than followed. `is_file()` alone follows the
    link; the loader shares `sitting.moved`'s resolve-and-bound rule instead.
    """
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET\n", encoding="utf-8")
    case_dir = _copy_case(CORPUS_DIR / "01-payments-checkout", tmp_path)
    source = case_dir / "source.md"
    source.unlink()
    source.symlink_to(outside)

    with pytest.raises(CorpusError, match="inside the case directory"):
        load_case(case_dir)


def test_the_two_readers_agree_the_escaping_source_is_out(tmp_path):
    """The loader and the digest reader answer "outside" the same way.

    Tested against each other, not each against its own expectation: `moved`
    treats the escaping symlink as stale, and `load_case` refuses it. A change
    that relaxes one without the other fails here.
    """
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET\n", encoding="utf-8")
    case_dir = _copy_case(CORPUS_DIR / "01-payments-checkout", tmp_path)
    source = case_dir / "source.md"
    source.unlink()
    source.symlink_to(outside)

    assert moved(case_dir, {"source.md": "0" * 64}) == ["source.md"]
    with pytest.raises(CorpusError):
        load_case(case_dir)


def test_a_source_over_the_ceiling_is_refused_unread(tmp_path):
    """An oversize source raises rather than buffering the whole file.

    The bound turns a symlink to a very large readable file from an
    out-of-memory into a clean corpus error.
    """
    case_dir = _copy_case(CORPUS_DIR / "01-payments-checkout", tmp_path)
    (case_dir / "source.md").write_bytes(b"x" * (MAX_CORPUS_SOURCE_BYTES + 1))

    with pytest.raises(CorpusError, match="source ceiling"):
        load_case(case_dir)


def test_missing_file_fails_closed(tmp_path):
    case_dir = _copy_case(CORPUS_DIR / "03-batch-data-pipeline", tmp_path)
    (case_dir / "claims" / "stride.json").unlink()

    with pytest.raises(CorpusError, match="claims/stride.json does not exist"):
        load_case(case_dir)


def test_case_id_must_match_directory(tmp_path):
    case_dir = _copy_case(CORPUS_DIR / "04-ml-inference-service", tmp_path)
    meta = json.loads((case_dir / "case.json").read_text())
    meta["id"] = "renamed"
    (case_dir / "case.json").write_text(json.dumps(meta))

    with pytest.raises(CorpusError, match="does not match the directory name"):
        load_case(case_dir)


def test_empty_corpus_fails_closed(tmp_path):
    with pytest.raises(CorpusError, match="no cases"):
        load_corpus(tmp_path)


def test_a_retired_field_says_where_the_record_went(tmp_path):
    """The refusal is a sentence the reader can act on, not a pydantic frame.

    A hard cutover leaves a working tree carrying the field it retired, and
    the launch that refuses it is where the reader meets it. ``str`` of a
    ``ValidationError`` names an error tag, a truncated repr and a
    documentation link — none of which say what to change.
    """
    case_dir = _copy_case(CORPUS_DIR / "02-iot-fleet-telemetry", tmp_path)
    meta = json.loads((case_dir / "case.json").read_text())
    meta["reviews"] = [{"submitted_by": "ada", "document": "REVIEW-ada.md"}]
    (case_dir / "case.json").write_text(json.dumps(meta))

    with pytest.raises(CorpusError) as raised:
        load_case(case_dir)

    said = str(raised.value)
    assert "02-iot-fleet-telemetry" in said
    assert "reviews is not a field case.json has" in said
    assert RETIRED_FIELDS["reviews"] in said
    assert "pydantic" not in said and "extra_forbidden" not in said


def test_an_unknown_field_is_refused_without_advice(tmp_path):
    """Only a field somebody retired has somewhere to point."""
    case_dir = _copy_case(CORPUS_DIR / "02-iot-fleet-telemetry", tmp_path)
    meta = json.loads((case_dir / "case.json").read_text())
    meta["reviewer"] = "ada"
    (case_dir / "case.json").write_text(json.dumps(meta))

    with pytest.raises(CorpusError, match="reviewer is not a field case.json has"):
        load_case(case_dir)


def test_a_field_with_a_wrong_value_still_names_the_field(tmp_path):
    """The plain form covers every rejection, not the extra field alone."""
    case_dir = _copy_case(CORPUS_DIR / "02-iot-fleet-telemetry", tmp_path)
    meta = json.loads((case_dir / "case.json").read_text())
    del meta["title"]
    (case_dir / "case.json").write_text(json.dumps(meta))

    with pytest.raises(CorpusError, match="title: Field required"):
        load_case(case_dir)


def test_the_retired_field_names_a_path_the_code_writes():
    """A path in prose drifts; this pins it to the module that writes there."""
    assert envelopes.SUBMISSIONS_DIR.as_posix() in RETIRED_FIELDS["reviews"]
    assert str(sittings.draft_root()) in RETIRED_FIELDS["reviews"].replace(
        "~", str(Path.home())
    )


class TestTheFlowMapServesTheCitationsItIsAskedAbout:
    """``flows_by_case`` against an analysed model that renames a flow (#949).

    ``endpoint_form`` resolves a cited flow by looking its ID up in this map and
    keeps the raw ID when the lookup misses. A map holding only blessed flows
    therefore leaves an end-to-end candidate's citation opaque, so it matches
    nothing — and that is invisible in every mode that injects the blessed
    model, which is every scored sweep to date.
    """

    @staticmethod
    def _renamed(case):
        """The case's model with one flow's label changed and nothing else."""
        raw = case.model.model_dump(mode="json")
        blessed = raw["data_flows"][0]
        original = blessed["id"]
        source, destination = blessed["source"], blessed["destination"]
        blessed["name"] = f"{blessed['name']} differently put"
        model = normalize_element_ids(SystemModel.model_validate(raw))
        moved = next(
            flow
            for flow in model.data_flows
            if (flow.source, flow.destination) == (source, destination)
        )
        assert moved.id != original, "the fixture must actually move the ID"
        return model, original, moved.id

    def test_a_renamed_flow_resolves_to_the_same_endpoints_as_the_blessed_one(self):
        corpus = load_corpus(CORPUS_DIR)
        case = corpus[0]
        analysed, blessed_id, renamed_id = self._renamed(case)

        flows = flows_by_case(corpus, {case.id: analysed})[case.id]

        assert flows[renamed_id] == flows[blessed_id]

    def test_without_the_analysed_model_the_renamed_id_is_unknown(self):
        """The defect, driven: the map cannot answer for the citation."""
        corpus = load_corpus(CORPUS_DIR)
        case = corpus[0]
        _, _, renamed_id = self._renamed(case)

        assert renamed_id not in flows_by_case(corpus)[case.id]

    def test_the_two_citations_then_carry_one_resolved_place(self):
        """What the identity rule reads, rather than what this function returns."""
        corpus = load_corpus(CORPUS_DIR)
        case = corpus[0]
        analysed, blessed_id, renamed_id = self._renamed(case)
        flows = flows_by_case(corpus, {case.id: analysed})[case.id]

        assert endpoint_form([blessed_id], flows) == endpoint_form([renamed_id], flows)
        # And without it, the candidate's citation stays opaque and cannot match.
        bare = flows_by_case(corpus)[case.id]
        assert endpoint_form([renamed_id], bare) == frozenset({renamed_id})

    def test_a_blessed_entry_wins_a_shared_id(self):
        """A collision resolves the reference against its own graph, never the
        extraction's — the substitution the identity module refuses."""
        corpus = load_corpus(CORPUS_DIR)
        case = corpus[0]
        blessed = case.model.data_flows[0]
        raw = case.model.model_dump(mode="json")
        # Same ID, endpoints swapped: a graph that disagrees about the flow.
        raw["data_flows"][0]["source"] = blessed.destination
        raw["data_flows"][0]["destination"] = blessed.source
        conflicting = SystemModel.model_validate(raw)

        flows = flows_by_case(corpus, {case.id: conflicting})[case.id]

        assert flows[blessed.id] == (blessed.source, blessed.destination)

    def test_a_case_with_no_analysed_model_is_unchanged(self):
        """Every other case's map is what it was, so one run cannot reach another."""
        corpus = load_corpus(CORPUS_DIR)
        analysed, _, _ = self._renamed(corpus[0])

        widened = flows_by_case(corpus, {corpus[0].id: analysed})
        bare = flows_by_case(corpus)

        assert [case.id for case in corpus[1:]]
        for case in corpus[1:]:
            assert widened[case.id] == bare[case.id]


#: A shipped case whose rulings file names reference 5.
RULED_CASE = "02-iot-fleet-telemetry"


def _ruled_case(tmp_path: Path, edit=lambda rulings: None) -> Path:
    case_dir = _copy_case(CORPUS_DIR / RULED_CASE, tmp_path)
    rulings = json.loads((CORPUS_DIR / RULED_CASE / "rulings.json").read_text())
    edit(rulings)
    (case_dir / "rulings.json").write_text(json.dumps(rulings))
    return case_dir


def test_a_ruled_reading_loads_beside_the_claim_it_names(tmp_path):
    case = load_case(_ruled_case(tmp_path))
    claim = case.stride_claims()[5]
    readings = case.stride_readings(5)
    assert readings[0] == (claim.verb, claim.affected_element_ids)
    assert len(readings) == 2


def test_a_case_without_rulings_reads_its_claims_alone(tmp_path):
    case = load_case(_copy_case(CORPUS_DIR / RULED_CASE, tmp_path))
    assert case.stride_readings(5) == (
        (case.stride_claims()[5].verb, case.stride_claims()[5].affected_element_ids),
    )


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (
            lambda r: r["stride"][0]["reference"].update(verb="read"),
            "names 0 reference claims",
        ),
        (
            lambda r: r["stride"][0]["also_acceptable"].update(
                affected_element_ids=["process:does-not-exist"]
            ),
            "not in the model",
        ),
        (
            lambda r: r["stride"][0]["also_acceptable"].update(verb="flood"),
            "is not a repudiation verb",
        ),
        (lambda r: r.update(case="01-payments-checkout"), "names case"),
        (lambda r: r.update(asvs=[]), "rulings.json"),
    ],
    ids=["claim-moved", "dangling-element", "verb-outside-lane", "wrong-case", "asvs"],
)
def test_a_ruling_that_no_longer_fits_its_claim_fails_closed(tmp_path, edit, message):
    with pytest.raises(CorpusError, match=message):
        load_case(_ruled_case(tmp_path, edit))
