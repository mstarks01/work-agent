"""Each backend value is a row of a table, and each table answers for its registry.

A backend is never a branch. A row with a field left out, or a value its
table has no row for, fails here rather than at a deployment's first job.
The registry of each table is the ``Literal`` that names its values.
"""

from __future__ import annotations

from dataclasses import MISSING, fields
from typing import get_args

import pytest

from analysis_service.jobs import JOB_STORES, JobStoreBackend, JobStoreName
from analysis_service.objects import OBJECT_STORES, ObjectName, ObjectStoreName
from analysis_service.sql_store import (
    DATABASE_CREDENTIALS,
    DATABASE_IAM,
    DATABASES,
    DIGEST_COLUMNS,
    Database,
    DatabaseCredentials,
    DatabaseDriver,
    DatabaseIam,
    DatabaseIamName,
)
from tests.sql_stores import OBJECT_ENVS, ObjectStoreEnv
from tests.test_job_store import BACKEND_PAIRS

TABLES = [
    (JOB_STORES, JobStoreName),
    (OBJECT_STORES, ObjectStoreName),
    (DATABASES, DatabaseDriver),
    (DATABASE_CREDENTIALS, DatabaseCredentials),
    (DATABASE_IAM, DatabaseIamName),
    (OBJECT_ENVS, ObjectStoreName),
    (DIGEST_COLUMNS, ObjectName),
]


@pytest.mark.parametrize(("table", "registry"), TABLES)
def test_each_table_answers_for_every_value_of_its_registry(table, registry):
    assert set(table) == set(get_args(registry))


@pytest.mark.parametrize(
    "row", [JobStoreBackend, Database, DatabaseIam, ObjectStoreEnv]
)
def test_a_row_states_every_fact(row):
    """A default is how a new row stays silent about a fact."""
    defaulted = [
        field.name
        for field in fields(row)
        if field.default is not MISSING or field.default_factory is not MISSING
    ]
    assert defaulted == []


def test_the_contract_suite_runs_on_every_backend_pair():
    """A new job store, database or object store needs its pair in the suite."""
    pairs = set()
    for name, backend in JOB_STORES.items():
        if not backend.objects:
            pairs.add(name)
            continue
        pairs |= {
            f"{name}/{driver}/{objects}"
            for driver in DATABASES
            for objects in OBJECT_STORES
        }
    assert set(BACKEND_PAIRS) == pairs
