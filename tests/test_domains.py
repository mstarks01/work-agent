"""Domain pack selection: which reference material a System Model earns."""

import pytest

from stride_service.domains import (
    DETECTORS,
    MAX_PACKS,
    pack_evidence,
    select_domain_packs,
)
from stride_service.system_model import (
    DataFlow,
    DataStore,
    Process,
    SystemModel,
    TrustBoundary,
)


def model_with(*, processes=(), stores=(), flows=(), boundaries=()):
    return SystemModel(
        processes=list(processes),
        data_stores=list(stores),
        data_flows=list(flows),
        trust_boundaries=list(boundaries)
        or [TrustBoundary(id="boundary:z", name="Z", kind="network")],
    )


def process(name, technology="python", zone="boundary:z"):
    return Process(
        id=f"process:{name}",
        name=name,
        technology=technology,
        trust_zone=zone,
        exposure="internal",
        interface_kind="non-web",
    )


def store(name, technology):
    return DataStore(
        id=f"store:{name}",
        name=name,
        technology=technology,
        trust_zone="boundary:z",
        data_classification="internal",
        encryption_at_rest="unknown",
    )


def flow(label, **overrides):
    fields = {
        "protocol": "HTTPS",
        "authentication": "workload identity",
        "data_description": "records",
        "encryption_in_transit": "TLS 1.3",
    }
    fields.update(overrides)
    return DataFlow(
        id=f"flow:a-to-b:{label}",
        name=label,
        source="process:a",
        destination="process:b",
        **fields,
    )


class TestDetection:
    def test_http_terms_select_the_api_pack(self):
        model = model_with(processes=[process("a"), process("b")], flows=[flow("call")])
        assert "http-api" in select_domain_packs(model)

    def test_database_technology_selects_the_database_pack(self):
        model = model_with(stores=[store("orders", "Cloud SQL Postgres")])
        assert select_domain_packs(model) == ("databases",)

    def test_oauth_in_authentication_selects_the_identity_pack(self):
        model = model_with(
            processes=[process("a"), process("b")],
            flows=[flow("login", authentication="OIDC via the broker", protocol="tcp")],
        )
        assert "oauth-oidc" in select_domain_packs(model)

    def test_a_tenant_boundary_selects_multi_tenancy_without_the_word(self):
        """The model states multi-tenancy in its own vocabulary; prose is optional."""
        model = model_with(
            boundaries=[TrustBoundary(id="boundary:t", name="Cust A", kind="tenant")]
        )
        assert select_domain_packs(model) == ("multi-tenant-saas",)

    def test_terms_match_on_word_boundaries(self):
        """``sso`` must not fire on ``lasso``, or selection is noise."""
        model = model_with(processes=[process("lasso", technology="lasso runtime")])
        assert "oauth-oidc" not in select_domain_packs(model)

    def test_an_unremarkable_model_earns_nothing(self):
        model = model_with(processes=[process("batch", technology="a shell script")])
        assert select_domain_packs(model) == ()


class TestSelection:
    def test_selection_is_capped(self):
        model = model_with(
            processes=[process("api", technology="FastAPI over HTTPS")],
            stores=[store("orders", "Postgres")],
            flows=[flow("login", authentication="OAuth bearer token")],
            boundaries=[TrustBoundary(id="boundary:t", name="T", kind="tenant")],
        )
        assert len(pack_evidence(model)) > MAX_PACKS
        assert len(select_domain_packs(model)) == MAX_PACKS

    def test_the_best_evidenced_packs_win(self):
        model = model_with(
            stores=[store(f"s{n}", "Postgres") for n in range(3)],
            processes=[process("api", technology="FastAPI")],
        )
        assert select_domain_packs(model)[0] == "databases"

    def test_ties_break_on_declaration_order(self):
        model = model_with(
            processes=[process("api", technology="FastAPI")],
            stores=[store("cache", "Redis")],
        )
        order = list(DETECTORS)
        selected = select_domain_packs(model)
        assert [order.index(pack) for pack in selected] == sorted(
            order.index(pack) for pack in selected
        )

    def test_selection_is_stable_across_calls(self):
        model = model_with(
            processes=[process("api", technology="FastAPI over HTTPS")],
            stores=[store("orders", "Postgres")],
        )
        assert select_domain_packs(model) == select_domain_packs(model)


@pytest.mark.parametrize("pack", sorted(DETECTORS))
def test_every_detector_names_a_pack_that_exists(pack):
    from pathlib import Path

    # The shared root: a pack describes a technology rather than a framework,
    # so every carried framework's lanes may earn it (ADR 0011).
    domains = Path(__file__).resolve().parents[1] / "domains"
    assert (domains / f"{pack}.md").is_file()
