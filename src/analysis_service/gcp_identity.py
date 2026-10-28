"""The platform identity that the ``iam`` mode of the ``gcs`` object store reads.

``google.auth.default()`` reads ``GOOGLE_APPLICATION_CREDENTIALS`` first, then
the gcloud user file, then the metadata server, and no parameter limits that
chain. The type of the credential that it returns names its source.
:func:`platform_credentials` accepts only the type that the metadata server of
Cloud Run, GKE Workload Identity and GCE gives (ADR 0105), so a key file, a
user login, an impersonated credential and Workload Identity Federation each
stop the startup.

The ``gcs`` extra holds the client (ADR 0103). :func:`platform_credentials`
imports ``google.auth`` on each call, so a missing library raises
:class:`ModuleNotFoundError` in the caller's startup. The ``gcs`` row is the
only reader of this module. The Vertex vendor row keeps the ADC chain.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from analysis_service.store_settings import StoreConfigError

if TYPE_CHECKING:
    from google.auth.compute_engine import Credentials

#: The one OAuth scope of the token: read and write of objects, and no other
#: API of the identity.
STORAGE_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"

#: The variable that ``google.auth.default()`` reads first.
APPLICATION_CREDENTIALS_VAR = "GOOGLE_APPLICATION_CREDENTIALS"

#: The type that the metadata server gives, as the refusal names it.
ACCEPTED_TYPE = "google.auth.compute_engine.Credentials"


def platform_credentials(label: str) -> Credentials:
    """The credential of the metadata server, with :data:`STORAGE_SCOPE` only.

    Each other type that ``google.auth.default()`` returns stops the startup,
    and so does a chain that finds no credential. ``label`` names the setting
    in the error, which names the accepted type and the type found, and no
    credential and no path.
    """
    import google.auth
    from google.auth import compute_engine
    from google.auth.exceptions import DefaultCredentialsError

    try:
        credentials, _ = google.auth.default(scopes=[STORAGE_SCOPE])
    except DefaultCredentialsError:
        found = "no credential"
    else:
        if isinstance(credentials, compute_engine.Credentials):
            return credentials
        kind = type(credentials)
        found = f"{kind.__module__}.{kind.__qualname__}"
    source = (
        f"; it reads {APPLICATION_CREDENTIALS_VAR} first, so unset it"
        if os.environ.get(APPLICATION_CREDENTIALS_VAR, "").strip()
        else ""
    )
    raise StoreConfigError(
        f"{label} reads only the identity of the metadata server"
        f" ({ACCEPTED_TYPE}), and google.auth found {found}{source}"
    )
