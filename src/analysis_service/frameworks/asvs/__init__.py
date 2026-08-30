"""The ASVS framework package: a published standard, as a package.

Nine members and a text root at ``frameworks/asvs/``, exactly like STRIDE. What
differs is what stands behind them. STRIDE is a method, so its ``version`` names
this repo's ruleset and it carries no catalog. ASVS is a published standard, so
``version`` names the standard's own release and the 345 requirements live in
:mod:`analysis_service.frameworks.asvs.catalog` as this package's private data.

**One lane is one chapter.** The standard's own applicability guidance operates
on a chapter — "for a machine-to-machine API, the requirements in chapter V3
related to web frontends will not be relevant" — so a lane that is a chapter puts
the standard's unit and this service's unit in the same place. The cost is real:
one lane is one **Model Tier** call, so an ASVS job runs 17 ``strong``-tier calls
against STRIDE's 6.

**This package reports no pass.** ASVS verification needs source code,
configuration and the development team, and a job here carries prose. See
:mod:`analysis_service.frameworks.asvs.record` for what the three **Verdict**
states carry instead, and ``disclaimer.md`` for what the report's reader is told.
"""

from __future__ import annotations

from types import MappingProxyType

from analysis_service.frameworks import (
    FrameworkPackage,
    IdRule,
    KnowledgeTables,
)
from analysis_service.frameworks.asvs.catalog import (
    ASVS_VERSION,
    CHAPTER_NUMBERS,
    LANES,
    is_published_requirement,
)
from analysis_service.frameworks.asvs.record import (
    ASVS_ID_FORMAT,
    AsvsOptions,
    DraftRequirementRuling,
)
from analysis_service.frameworks.asvs.rules import RULES, asvs_precondition

__all__ = ["ASVS", "AsvsOptions"]


# One note per recurring applicability question, against the rules that select
# it. Document-to-rules like STRIDE's, and for the same reason: what a
# maintainer edits is a document and what it applies to.
#
# **Every rule appears.** Leaving the empty corpus put all 17 under
# ``test_every_rule_can_retrieve_something``, which is the stated cost of
# starting one. A rule with a lead and nothing behind it is a gap.
NOTES: dict[str, tuple[str, ...]] = {
    "injection-sinks-and-context": (
        "encoding-and-sanitization-database",
        "encoding-and-sanitization-rich-text-input",
    ),
    "xml-and-parser-surface": ("encoding-and-sanitization-xml-parser",),
    "where-the-rule-is-enforced": (
        "validation-and-business-logic-client-side-code",
        "validation-and-business-logic-multi-step-flow",
        "validation-and-business-logic-crossing-from-an-entity",
    ),
    "browser-delivered-controls": (
        "web-frontend-security-browser-frontend",
        "web-frontend-security-cookies",
    ),
    "cross-origin-and-handshake": (
        "web-frontend-security-cors",
        "api-and-web-service-websocket",
    ),
    "untrusted-file-intake": ("file-handling-file-upload",),
    "proving-identity": (
        "authentication-authentication",
        "authentication-password-auth",
    ),
    "session-and-token-lifetime": (
        "session-management-sessions",
        "self-contained-tokens-self-contained-tokens",
    ),
    "delegated-authorization": ("oauth-and-oidc-oauth",),
    "algorithms-and-key-custody": ("cryptography-encryption",),
    "sensitive-data-in-the-client": ("data-protection-browser-frontend",),
    "who-may-reach-what": (
        "authorization-privileged-role",
        "authorization-shared-account",
    ),
    "the-channel-itself": (
        "secure-communication-transport",
        "secure-communication-unverified-transit",
    ),
    "where-the-secret-lives": ("configuration-secret-material",),
    "code-you-did-not-write": ("secure-coding-and-architecture-third-party-component",),
    "what-reaches-the-log": (
        "security-logging-and-error-handling-log-or-audit-trail",
        "security-logging-and-error-handling-write-with-no-record",
    ),
    "real-time-media-and-signalling": ("webrtc-real-time-media",),
}

# The worked cases. These teach **this framework's own judgement**, which is
# what separates them from STRIDE's: every one turns on ruling applicability
# rather than on grading harm, and three of the six exist to keep a reader from
# reporting a pass this service cannot reach.
#
# Selected across lanes on purpose. "A stated control is not a verification" is
# not a property of cryptography, and the agent that needs it is whichever
# lane's rule fired.
CASES: dict[str, tuple[str, ...]] = {
    "applies-and-the-input-cannot-settle-it": (
        "authentication-password-auth",
        "session-management-sessions",
        "encoding-and-sanitization-database",
        "data-protection-classified-store",
    ),
    "a-stated-control-is-not-a-verification": (
        "cryptography-encryption",
        "authentication-authentication",
        "self-contained-tokens-self-contained-tokens",
        # The case's own example is "all traffic is TLS 1.3", which is this
        # lane's subject stated in the submitter's own words.
        "secure-communication-transport",
    ),
    "the-chapter-does-not-reach-this-system": (
        "web-frontend-security-browser-frontend",
        "web-frontend-security-cors",
        "api-and-web-service-websocket",
        # The chapter most systems genuinely do not have, which is what this
        # case is about.
        "webrtc-real-time-media",
    ),
    "the-neighbouring-chapters-requirement": (
        "oauth-and-oidc-oauth",
        "validation-and-business-logic-client-side-code",
        # The case's worked example is a ruling that belongs to authorization
        # and was nearly filed under authentication.
        "authorization-privileged-role",
    ),
    "the-requirement-that-asks-for-a-document": (
        "file-handling-file-upload",
        "validation-and-business-logic-multi-step-flow",
        "encoding-and-sanitization-rich-text-input",
        # Both chapters open with a documentation requirement, which is what
        # this case exists to stop an agent skipping.
        "configuration-secret-material",
        "secure-coding-and-architecture-third-party-component",
    ),
    "one-fact-two-chapters": (
        "web-frontend-security-cookies",
        "data-protection-browser-frontend",
        "encoding-and-sanitization-xml-parser",
        # A shared account is one fact that rules in two chapters: where the
        # secret lives, and why the log cannot attribute the action.
        "security-logging-and-error-handling-log-or-audit-trail",
    ),
}


ASVS = FrameworkPackage(
    name="asvs",
    version=ASVS_VERSION,
    lanes=LANES,
    rules=RULES,
    record=DraftRequirementRuling,
    id_rule=IdRule(
        # The standard's own version-safe reference, composed from the lane's
        # chapter number and the agent's ``<section>.<requirement>`` key. The
        # chapter therefore appears in the ID, in the lane and in the record from
        # one call, so the three cannot disagree.
        template=ASVS_ID_FORMAT,
        prefix=CHAPTER_NUMBERS,
        lane_field="chapter",
        # The one thing the key's shape cannot say. ``99.99`` is as well-formed
        # as ``2.1``, so without this the service composes the standard's own
        # version-safe reference for a requirement the standard does not
        # publish. STRIDE declares no predicate because it mints its own IDs.
        known=is_published_requirement,
    ),
    options=AsvsOptions,
    precondition=asvs_precondition,
    knowledge=KnowledgeTables(
        notes=MappingProxyType(NOTES), cases=MappingProxyType(CASES)
    ),
)
