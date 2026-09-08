"""Metamorphic checks over the mechanical seams (#675 D26).

Every hand-picked case in this suite is one input the author thought of. The
defects the audits kept finding were shapes nobody listed — U+2028 in a value,
an element order that moved a number, a rename that split a key. So these
tests generate small valid models from a seed, apply one transformation whose
effect on the answer is known, and check the answer moved exactly that way:
not at all for a reorder, one-to-one for a rename, refusal for a duplicate.

No property library: a seeded ``random.Random`` and the shipped ID normalizer
are enough to build a valid model, and a failing seed is a line in the
parametrize list rather than a shrunk example nobody can reproduce.
"""

from __future__ import annotations

import json
import random

import pytest

from analysis_service.analysis import unknown_controls
from analysis_service.candidates import generate_candidates
from analysis_service.coverage import build_coverage
from analysis_service.critic import duplicate_groups
from analysis_service.evidence import evidence_catalog
from analysis_service.fan_in import join_drafts
from analysis_service.frameworks import PACKAGES
from analysis_service.frameworks.stride import STRIDE
from analysis_service.graph import render, render_fenced, unfence
from analysis_service.grounding import normalize
from analysis_service.report import Report
from analysis_service.sources import DEFAULT_DESCRIPTION_LABEL
from analysis_service.system_model import ELEMENT_GROUPS, SystemModel
from analysis_service.validation import parse_and_validate
from tests.factories import sample_draft, valid_model
from tests.test_structural_readers import sound_report

SEEDS = list(range(20))

_CONTROLS = [
    "unknown",
    "none",
    "mTLS",
    "session cookie",
    "unknown; hedged",
    "none; by position",
]
_CLASSIFICATIONS = ["confidential", "public", "unknown", "internal"]
_PROTOCOLS = ["HTTPS", "gRPC", "unknown", "Postgres wire protocol"]
_OPERATIONS = ["read", "write", "read-write", "unknown"]
#: The line terminators ``str.splitlines`` knows and ``"\\n"`` does not cover.
_TERMINATORS = ["\u2028", "\u2029", "\x85", "\r\n", "\r", "\n"]
#: What a caller's bytes can hold that a fence or a JSON renderer must survive.
_HAZARDS = [*_TERMINATORS, "```", "````", "\u201c`\u201d", "\\"]


def _element(prefix: str, index: int, boundary: str, rng: random.Random, **fields):
    return {
        "id": f"{prefix}:{index}",
        "name": f"{prefix.title()} {index} {rng.choice(['alpha', 'beta', 'gamma'])}",
        "trust_zone": boundary,
        "source_excerpt": "",
        "source_label": "",
        **fields,
    }


def generated(seed: int) -> dict:
    """One small valid model, as the extractor would have emitted it.

    Provisional IDs, because the shipped normalizer derives the real ones from
    the names and repoints every flow endpoint, and that is the deriver a
    test should exercise rather than a copy of it.
    """
    rng = random.Random(seed)
    boundaries = [
        f"boundary:{name}" for name in ("edge", "core", "vault")[: rng.randint(2, 3)]
    ]
    entities = [
        _element(
            "entity",
            i,
            boundaries[0],
            rng,
            kind=rng.choice(["human", "external-system"]),
            assets=[],
        )
        for i in range(rng.randint(1, 3))
    ]
    processes = [
        _element(
            "process",
            i,
            rng.choice(boundaries),
            rng,
            technology="svc",
            exposure=rng.choice(["internet-facing", "internal", "unknown"]),
            interface_kind=rng.choice(["web", "non-web", "unknown"]),
        )
        for i in range(rng.randint(1, 4))
    ]
    stores = [
        _element(
            "store",
            i,
            rng.choice(boundaries),
            rng,
            technology="db",
            data_classification=rng.choice(_CLASSIFICATIONS),
            encryption_at_rest=rng.choice(_CONTROLS),
            assets=rng.choice([[], ["pii"]]),
        )
        for i in range(rng.randint(0, 3))
    ]
    sources = [e["id"] for e in entities] + [p["id"] for p in processes]
    targets = [p["id"] for p in processes] + [s["id"] for s in stores]
    flows = []
    for i in range(rng.randint(1, 6)):
        src, dst = rng.choice(sources), rng.choice(targets)
        if src == dst:
            continue
        flows.append(
            {
                "id": f"flow:{i}",
                "name": f"Flow {i}",
                "source": src,
                "destination": dst,
                "protocol": rng.choice(_PROTOCOLS),
                "authentication": rng.choice(_CONTROLS),
                "data_description": "payload",
                "encryption_in_transit": rng.choice(_CONTROLS),
                "operations": rng.choice(_OPERATIONS),
                "source_excerpt": "",
                "source_label": "",
            }
        )
    return {
        "external_entities": entities,
        "processes": processes,
        "data_stores": stores,
        "data_flows": flows,
        "trust_boundaries": [
            {
                "id": b,
                "name": b.split(":")[1].title(),
                "kind": "network",
                "source_excerpt": "",
                "source_label": "",
            }
            for b in boundaries
        ],
        "assumptions": [],
    }


def normalized(data: dict) -> SystemModel:
    model, issues = parse_and_validate(data, normalize_ids=True)
    assert model is not None and not issues, [issue.message for issue in issues]
    return model


def shuffled(data: dict, seed: int) -> dict:
    rng = random.Random(seed + 1_000)
    out = dict(data)
    for group in (*ELEMENT_GROUPS, "assumptions"):
        entries = list(data[group])
        rng.shuffle(entries)
        out[group] = entries
    return out


def facts(model: SystemModel) -> dict:
    """Every derived fact this module holds invariant under a reorder, as sets."""
    return {
        "catalog": {
            ref: ground.model_dump() for ref, ground in evidence_catalog(model).items()
        },
        "crossings": {
            json.dumps(c.model_dump(mode="json"), sort_keys=True)
            for c in model.boundary_crossings()
        },
        "controls": {
            (c.element_id, c.attribute, c.state) for c in unknown_controls(model)
        },
        "ids": {e.id for e in model.elements()},
    }


@pytest.mark.parametrize("seed", SEEDS)
def test_element_order_changes_no_derived_fact(seed):
    data = generated(seed)
    assert facts(normalized(data)) == facts(normalized(shuffled(data, seed)))


@pytest.mark.parametrize("seed", SEEDS)
def test_a_stable_renaming_maps_every_derived_key_one_to_one(seed):
    """Rename one element; every catalog key moves by the ID map and nothing else."""
    data = generated(seed)
    before = normalized(data)
    rng = random.Random(seed + 2_000)
    group = rng.choice(
        [g for g in ELEMENT_GROUPS if data[g] and g != "trust_boundaries"]
    )
    data[group][0]["name"] = data[group][0]["name"] + " renamed"
    after = normalized(data)
    id_map = dict(
        zip(
            (e.id for e in before.elements()),
            (e.id for e in after.elements()),
            strict=True,
        )
    )
    assert len(set(id_map.values())) == len(id_map)

    def translate(ref: str) -> str:
        for old, new in sorted(id_map.items(), key=lambda kv: -len(kv[0])):
            ref = ref.replace(old, new)
        return ref

    assert {translate(k) for k in evidence_catalog(before)} == set(
        evidence_catalog(after)
    )
    assert {id_map[c.element_id] for c in unknown_controls(before)} == {
        c.element_id for c in unknown_controls(after)
    }


@pytest.mark.parametrize("seed", SEEDS)
def test_a_duplicated_element_is_refused_by_name(seed):
    data = generated(seed)
    group = random.Random(seed).choice([g for g in ELEMENT_GROUPS if data[g]])
    data[group].append(dict(data[group][0]))
    _, issues = parse_and_validate(data, normalize_ids=True)
    assert "duplicate-id" in {issue.code for issue in issues}


@pytest.mark.parametrize("seed", SEEDS)
def test_the_model_round_trips_through_json(seed):
    model = normalized(generated(seed))
    assert SystemModel.model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_a_report_round_trips_through_json(framework):
    report = sound_report(framework)
    again = Report.model_validate_json(report.model_dump_json())
    assert again.model_dump(mode="json") == report.model_dump(mode="json")


@pytest.mark.parametrize("terminator", _HAZARDS)
def test_every_line_terminator_survives_the_fence(terminator):
    """The shape ``unfence`` once missed: a payload holding U+2028 round-tripped corrupted."""
    value = {
        "notes": f"before{terminator}after",
        "quote": f"tick{terminator}```{terminator}",
    }
    assert unfence(render_fenced(value)) == render(value)
    assert json.loads(unfence(render_fenced(value))) == value


@pytest.mark.parametrize("terminator", _TERMINATORS)
def test_normalize_is_idempotent_and_terminator_blind(terminator):
    text = f"The service{terminator}does not encrypt “backups” at rest."
    once = normalize(text)
    assert normalize(once) == once
    assert once == normalize(text.replace(terminator, " "))


def test_lane_order_changes_nothing_the_join_produces():
    model = valid_model()
    sources = {
        DEFAULT_DESCRIPTION_LABEL: "Customers log in to the web app, which stores orders."
    }
    by_lane = {
        "spoofing": [sample_draft("S-01", "spoofing")],
        "tampering": [sample_draft("T-01", "tampering")],
    }
    forward = join_drafts(by_lane, STRIDE, model, sources)
    backward = join_drafts(
        dict(reversed(list(by_lane.items()))), STRIDE, model, sources
    )
    assert [d.id for d in forward.drafts] == [d.id for d in backward.drafts]
    assert forward.marks == backward.marks


@pytest.mark.parametrize("seed", SEEDS[:5])
def test_element_order_changes_no_coverage_row_or_duplicate_pair(seed):
    model = valid_model()
    data = model.model_dump(mode="json")
    reordered = SystemModel.model_validate(shuffled(data, seed))
    drafts = {"spoofing": [sample_draft("S-01"), sample_draft("S-02")]}
    for m in (model, reordered):
        assert duplicate_groups(drafts["spoofing"], m) == {
            "S-01": ["S-02"],
            "S-02": ["S-01"],
        }
    rows = lambda m: build_coverage(
        drafts, generate_candidates(m, STRIDE.lanes, STRIDE.rules), m, STRIDE
    )
    assert rows(model) == rows(reordered)
