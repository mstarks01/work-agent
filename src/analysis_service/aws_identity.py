"""The platform identity that each ``iam`` mode on AWS reads.

The ``iam`` mode of the ``s3`` object store and the ``rds`` token of a
database take no key from the deployment. ``boto3`` finds the credentials in
its own chain, and that chain also reads a static key pair from the
environment or from a profile file. :func:`platform_session` refuses each
credential that a platform did not issue, so a static key works only in the
``api_key`` mode, which reads it from its ``*_FILE`` settings.

The ``aws`` extra holds ``boto3`` (ADR 0023). :func:`platform_session`
imports it on each call, so a missing ``boto3`` raises
:class:`ModuleNotFoundError` in the caller's startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from analysis_service.store_settings import StoreConfigError

if TYPE_CHECKING:
    import boto3

#: The ``botocore`` credential methods of a platform identity: the role of an
#: ECS task or an EKS pod (``container-role``), the role of an EC2 instance
#: (``iam-role``), and the web identity token of a workload
#: (``assume-role-with-web-identity``). Each one issues temporary credentials
#: that ``botocore`` refreshes.
PLATFORM_METHODS = frozenset(
    {"container-role", "iam-role", "assume-role-with-web-identity"}
)


def platform_session(label: str) -> boto3.Session:
    """A ``boto3`` session whose credentials a platform issued.

    ``boto3`` resolves the credentials here, as it does when it builds a
    client. A static key pair, a profile, an SSO login or a credential process
    stops the startup, and so does a chain that finds no credentials.
    ``label`` names the setting in the error, which names the method and no
    credential.
    """
    import boto3

    session = boto3.Session()
    credentials = session.get_credentials()
    method = None if credentials is None else credentials.method
    if method not in PLATFORM_METHODS:
        found = "no credentials" if method is None else f"credentials from {method}"
        raise StoreConfigError(
            f"{label} reads only the identity of the platform"
            f" ({', '.join(sorted(PLATFORM_METHODS))}), and boto3 found {found}"
        )
    return session
