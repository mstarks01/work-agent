"""Principals and the owner IDs that stand for them (ADR 0064).

A **Principal** is one identity from one issuer: the pair of the issuer and the
``sub`` claim. The service gives each principal a random UUIDv4, the owner ID,
and every job, every ownership check and every log line names the owner by that
ID. A ``sub`` is unique only inside its issuer, and some identity providers put
an email address in it, so neither the ``sub`` nor a hash of it keys an owner.

A job store holds the principals. It creates one only in the operation that
admits the first job of its identity, so a refused submission and a read create
none. The engine and the web app have no token: they name each caller under
:data:`LOCAL_ISSUER`, which no OIDC issuer can be, because an OIDC issuer is an
``https`` URL. The ``/v1`` path refuses a token under that issuer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

logger = logging.getLogger(__name__)

#: The issuer of every principal the engine and the web app name. Reserved: the
#: ``/v1`` path refuses a token whose issuer is this value.
LOCAL_ISSUER = "local:"


@dataclass(frozen=True)
class Identity:
    """Who a caller is: the issuer, and the ``sub`` claim that issuer states."""

    issuer: str
    sub: str

    @classmethod
    def local(cls, caller: str) -> Identity:
        """The identity of an engine or web app caller, under the reserved issuer."""
        return cls(issuer=LOCAL_ISSUER, sub=caller)

    @property
    def is_local(self) -> bool:
        """Whether this identity is under the reserved issuer."""
        return self.issuer == LOCAL_ISSUER


@dataclass(frozen=True)
class Principal:
    """One identity the service knows, and the owner ID that stands for it.

    It holds these four fields and nothing more. The identity provider owns
    the account status, so a principal has no name, no email and no flag.
    """

    owner_id: str
    issuer: str
    sub: str
    created_at: datetime

    @classmethod
    def new(cls, identity: Identity) -> Principal:
        """A principal for ``identity`` with a new random owner ID.

        A UUIDv4, never a UUIDv7, which shows when it was made, and never a
        hash, which anyone who knows the ``sub`` can compute.
        """
        return cls(
            owner_id=str(uuid4()),
            issuer=identity.issuer,
            sub=identity.sub,
            created_at=datetime.now(UTC),
        )

    @property
    def identity(self) -> Identity:
        return Identity(issuer=self.issuer, sub=self.sub)


def log_created(principal: Principal) -> None:
    """Write the one log line that carries a ``sub``: the creation of a principal.

    Every other log line names the owner by its owner ID. The ``repr`` of each
    claim keeps a line break in it from forging a second record (CWE-117).
    """
    logger.info(
        "principal %s created for issuer %r, sub %r",
        principal.owner_id,
        principal.issuer,
        principal.sub,
    )


class PrincipalTable:
    """Principals held in process memory, keyed by identity."""

    def __init__(self) -> None:
        self._by_identity: dict[Identity, Principal] = {}
        self._owner_ids: set[str] = set()

    def find(self, identity: Identity) -> Principal | None:
        return self._by_identity.get(identity)

    def holds(self, owner_id: str) -> bool:
        """Whether a principal here has ``owner_id``."""
        return owner_id in self._owner_ids

    def add(self, principal: Principal) -> None:
        """Keep ``principal``. Its identity and its owner ID must be new here."""
        if principal.identity in self._by_identity or self.holds(principal.owner_id):
            raise ValueError(f"principal {principal.owner_id} is already held")
        self._by_identity[principal.identity] = principal
        self._owner_ids.add(principal.owner_id)
