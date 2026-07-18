"""Shared factory for a small, fully valid System Model.

Topology: customer (internet zone) -> web app -> orders db (both internal),
one cross-boundary flow and one intra-zone flow, one recorded assumption.
"""

from stride_service.system_model import (
    Assumption,
    DataFlow,
    DataStore,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
)


def valid_model() -> SystemModel:
    return SystemModel(
        external_entities=[
            ExternalEntity(
                id="entity:customer",
                name="Customer",
                kind="human",
                trust_zone="boundary:internet",
                source_excerpt="customers log in from the browser",
                assets=["pii"],
            )
        ],
        processes=[
            Process(
                id="process:web-app",
                name="Web App",
                technology="Python/FastAPI on Cloud Run",
                trust_zone="boundary:internal-network",
                exposure="internet-facing",
                source_excerpt="the web app runs on Cloud Run",
            )
        ],
        data_stores=[
            DataStore(
                id="store:orders-db",
                name="Orders DB",
                technology="Cloud SQL Postgres",
                trust_zone="boundary:internal-network",
                data_classification="confidential",
                encryption_at_rest="unknown",
                source_excerpt="orders are stored in Postgres",
                assets=["business-critical-data", "pii"],
            )
        ],
        data_flows=[
            DataFlow(
                id="flow:customer-to-web-app:login",
                name="Login",
                source="entity:customer",
                destination="process:web-app",
                protocol="HTTPS",
                authentication="session cookie",
                data_description="credentials in, session out",
                encryption_in_transit="TLS 1.3",
                source_excerpt="customers log in from the browser",
            ),
            DataFlow(
                id="flow:web-app-to-orders-db:store-order",
                name="Store Order",
                source="process:web-app",
                destination="store:orders-db",
                protocol="Postgres wire protocol",
                authentication="IAM database auth",
                data_description="order rows",
                encryption_in_transit="unknown",
                source_excerpt="orders are stored in Postgres",
            ),
        ],
        trust_boundaries=[
            TrustBoundary(
                id="boundary:internet",
                name="Internet",
                kind="network",
                source_excerpt="customers log in from the browser",
            ),
            TrustBoundary(
                id="boundary:internal-network",
                name="Internal Network",
                kind="network",
                source_excerpt="the web app runs on Cloud Run",
            ),
        ],
        assumptions=[
            Assumption(
                assumption="web app is internet-facing",
                element_id="process:web-app",
                basis="customers reach it directly from the browser",
            )
        ],
    )
