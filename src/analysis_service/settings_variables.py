"""The variables of a deployment's settings, joined from each loader family.

Each loader stays the only source of the variables it reads, and exports them
as ``env_variables()`` (ADR 0091). This module joins the exports and names the
variables that hold a secret file. It imports no module that needs an optional
extra, so a process without the ``sql`` or the ``aws`` extra can call it.
"""

from __future__ import annotations

from analysis_service import (
    deployment,
    model_tiers,
    resilience,
    sampling,
    store_backends,
    vendors,
)
from analysis_service.objects import S3_KEY_FILES
from analysis_service.store_settings import DATABASE_PASSWORD_FILE_VAR


def deployment_variables() -> tuple[str, ...]:
    """Every variable that a loader of a Deployment Setting reads, once each.

    The Settings page holds one entry for each. A loader that reads a new
    variable exports it, or the test of the page module fails. The ``/v1``
    auth settings and ``ANALYSIS_OFFLINE`` are not here: no Deployment reads
    them.
    """
    return tuple(
        dict.fromkeys(
            (
                *deployment.env_variables(),
                *model_tiers.env_variables(),
                *vendors.env_variables(),
                *sampling.env_variables(),
                *resilience.env_variables(),
                *store_backends.env_variables(),
            )
        )
    )


def secret_vars() -> frozenset[str]:
    """Every variable that names a secret file.

    **The one reader of that fact.** The Settings page, its write-only key
    field and the file permission check call it. It collects the secret
    credential variables of each vendor and mode, the key files of the ``s3``
    object store, and the database password file. A ``_FILE`` suffix does not
    make a secret: ``ANALYSIS_TIERS_FILE`` names a config file, and
    ``ANALYSIS_DATABASE_CA_FILE`` names a certificate.
    """
    vendor_secrets = {
        var
        for vendor in vendors.VENDORS.values()
        for mode in vendor.credentials
        for var in vendor.secret_env_vars(mode)
    }
    return frozenset(
        {*vendor_secrets, *S3_KEY_FILES.values(), DATABASE_PASSWORD_FILE_VAR}
    )
