"""What the application can do, as closed facts a framework's applicability reads.

A **Capability** is one thing the application has or does, such as a browser
frontend, OAuth, or files it accepts from its users. It is a fact about the
application as a whole, and most capabilities have no place on one element of
the **System Model**. A framework that publishes a catalog of requirements
reads capabilities to decide which requirements have a subject in this
application. It never reads them to decide whether a requirement is met: a
capability says that the application uses sessions, never how long a session
lasts.

**Three states, and silence is the third.** A capability is ``present``,
``absent`` or ``unknown``. A source that says nothing leaves it ``unknown``,
and nothing here turns ``unknown`` into ``absent``. Only a source, or an
answer, that states the absence makes it ``absent``.

**A capability can sit under a parent.** An OAuth role is a part of OAuth, and
a TURN server is a part of WebRTC. An absent parent makes each child absent,
and a present child makes its parent present. So one answer about the parent
settles every requirement that reads a child, and the question about a child
is worth asking only when the parent is present.

**The applicability rule is data.** A framework states, for each unit, an
expression over capability keys: ``always``, one key, or ``all`` and ``any``
over smaller expressions. :func:`evaluate` reads it with three-valued logic, so
an unknown term never counts as false. The expression names only presence. A
rule that needs a control to be missing, or a value to be set, is a
conformance question and does not belong here.

**The maintainer reviews the wording.** An agent drafted every row, and
``reviewed_by`` names who accepted it. A new row carries ``None`` until the
maintainer reads it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeAlias

__all__ = [
    "ALWAYS",
    "CAPABILITIES",
    "Applicability",
    "Capability",
    "CapabilityFact",
    "CapabilityNeed",
    "Decision",
    "Expression",
    "Presence",
    "evaluate",
    "expression_issues",
    "expression_keys",
    "lineage",
    "open_capability_needs",
    "resolve",
]

#: Whether the application has a capability. ``unknown`` is what silence is.
Presence = Literal["present", "absent", "unknown"]

#: Whether a unit of a framework applies to the application.
Applicability = Literal["applicable", "not-applicable", "unknown"]

#: The expression that holds for every application in the framework's scope.
ALWAYS = "always"

#: ``always``, one capability key, or ``{"all": [...]}`` / ``{"any": [...]}``
#: over smaller expressions. JSON-shaped, because a framework's table is data.
Expression: TypeAlias = "str | Mapping[str, Sequence[Expression]]"


@dataclass(frozen=True)
class Capability:
    """One thing the application can have or do."""

    #: What the capability is, in one sentence a reader of a report can check.
    meaning: str
    #: The question a person answers yes, no or "I don't know".
    question: str
    #: The capability this one is a part of, or ``""``.
    parent: str
    #: Who reviewed the wording, or ``None`` where nobody has.
    reviewed_by: str | None


def _row(meaning: str, question: str, parent: str = "") -> Capability:
    return Capability(
        meaning=meaning, question=question, parent=parent, reviewed_by=None
    )


CAPABILITIES: Mapping[str, Capability] = MappingProxyType(
    {
        # --- Clients and interfaces ---
        "browser-frontend": _row(
            "people use the application through pages or scripts it serves to a"
            " web browser",
            "Do people use the application through a web browser?",
        ),
        "native-client": _row(
            "people use the application through a mobile or desktop app that calls it",
            "Do people use the application through a mobile or desktop app?",
        ),
        "cookies": _row(
            "the application sets cookies in its responses",
            "Does the application set cookies?",
        ),
        "cors": _row(
            "the application answers requests from web pages on other origins (CORS)",
            "Does the application accept requests from web pages hosted on other"
            " domains (CORS)?",
        ),
        "websocket": _row(
            "the application holds WebSocket connections",
            "Does the application use WebSockets?",
        ),
        "graphql": _row(
            "the application serves a GraphQL API",
            "Does the application serve a GraphQL API?",
        ),
        "server-side-javascript": _row(
            "the application's server code runs as JavaScript, such as Node.js",
            "Does any server part of the application run JavaScript, such as Node.js?",
        ),
        "memory-unsafe-code": _row(
            "part of the application is written in a language without memory"
            " safety, such as C or C++",
            "Is any part of the application written in C, C++ or another"
            " language without memory safety?",
        ),
        # --- Interpreters and parsers the application feeds ---
        "database": _row(
            "the application queries a database with a query language (SQL,"
            " NoSQL, graph)",
            "Does the application query a database?",
        ),
        "os-command": _row(
            "the application runs operating system commands or programs",
            "Does the application run operating system commands or external programs?",
        ),
        "ldap": _row(
            "the application queries an LDAP directory",
            "Does the application query an LDAP directory?",
        ),
        "xpath": _row(
            "the application runs XPath queries",
            "Does the application run XPath queries?",
        ),
        "latex": _row(
            "the application renders LaTeX",
            "Does the application process LaTeX?",
        ),
        "jndi": _row(
            "the application performs JNDI lookups",
            "Does the application perform JNDI lookups?",
        ),
        "cache-service": _row(
            "the application stores data in a cache service such as memcached or Redis",
            "Does the application use a cache service such as memcached or Redis?",
        ),
        "mail-sending": _row(
            "the application sends or reads email through a mail server",
            "Does the application send or read email?",
        ),
        "template-engine": _row(
            "the application renders server-side templates",
            "Does the application render pages or messages from server-side templates?",
        ),
        "dynamic-regex": _row(
            "the application builds regular expressions from input it receives",
            "Does the application build regular expressions from user input,"
            " such as a pattern search?",
        ),
        "xml-parsing": _row(
            "the application parses XML, including SOAP and SVG",
            "Does the application parse XML, SOAP or SVG?",
        ),
        "rich-text-input": _row(
            "the application accepts markup from its users: HTML, SVG, Markdown,"
            " CSS, BBCode or templates",
            "Does the application accept formatted text or markup from users,"
            " such as HTML, Markdown or SVG?",
        ),
        "spreadsheet-export": _row(
            "the application exports data as CSV or spreadsheet files",
            "Does the application export data as CSV or spreadsheet files?",
        ),
        "outbound-http": _row(
            "the application's backend sends HTTP requests to other services",
            "Does the application's backend call other HTTP services or APIs?",
        ),
        "url-fetch": _row(
            "the application fetches a URL that a user or an outside party"
            " supplied, such as a webhook or a link preview",
            "Does the application fetch URLs that users or partners supply, such"
            " as webhooks or link previews?",
            parent="outbound-http",
        ),
        # --- Business behaviour ---
        "multi-step-flow": _row(
            "the application has a business flow of ordered steps, such as a"
            " checkout or a sign-up wizard",
            "Does the application have multi-step flows, such as a checkout or a"
            " wizard?",
        ),
        "limited-resource": _row(
            "the application sells, books or allocates things of limited quantity",
            "Does the application book or allocate things of limited quantity,"
            " such as seats, stock or time slots?",
        ),
        "high-value-flow": _row(
            "the application performs operations of high value, such as payments,"
            " transfers or contract approvals",
            "Does the application perform high-value operations, such as"
            " payments, transfers or approvals?",
        ),
        # --- Files ---
        "file-upload": _row(
            "the application accepts files from users or other systems, by"
            " upload or import",
            "Does the application accept files from users or other systems?",
        ),
        "archive-files": _row(
            "the application accepts compressed or archive files, such as zip",
            "Does the application accept compressed files, such as zip?",
            parent="file-upload",
        ),
        "image-files": _row(
            "the application accepts image files",
            "Does the application accept image files?",
            parent="file-upload",
        ),
        "file-download": _row(
            "the application sends files to its users, as downloads, exports or"
            " attachments",
            "Does the application send files to users, such as downloads or"
            " email attachments?",
        ),
        # --- Authentication ---
        "authentication": _row(
            "the application verifies who a user or a caller is",
            "Does the application require users or callers to sign in or authenticate?",
        ),
        "password-authentication": _row(
            "users authenticate to the application with a password it checks",
            "Do users sign in with a password that the application checks?",
            parent="authentication",
        ),
        "multi-factor-authentication": _row(
            "the application offers or requires a second authentication factor",
            "Does the application use a second authentication factor?",
            parent="authentication",
        ),
        "one-time-codes": _row(
            "the application authenticates with one-time codes: SMS, email,"
            " push, authenticator apps or recovery codes",
            "Does the application use one-time codes, such as SMS, email, push"
            " or authenticator-app codes?",
            parent="authentication",
        ),
        "biometric-authentication": _row(
            "the application accepts a biometric factor",
            "Does the application accept fingerprints, face or other biometrics?",
            parent="authentication",
        ),
        "cryptographic-authenticators": _row(
            "the application authenticates users with keys or devices: passkeys,"
            " security keys, smart cards or certificates",
            "Do users authenticate with passkeys, security keys, smart cards or"
            " certificates?",
            parent="authentication",
        ),
        "federated-identity": _row(
            "users sign in through a separate identity provider (SSO)",
            "Do users sign in through a separate identity provider or single sign-on?",
            parent="authentication",
        ),
        "saml": _row(
            "the application consumes SAML assertions",
            "Does the application use SAML?",
            parent="federated-identity",
        ),
        "mutual-tls": _row(
            "the application authenticates callers by TLS client certificates",
            "Does the application authenticate callers with TLS client"
            " certificates (mutual TLS)?",
        ),
        # --- Sessions and tokens ---
        "sessions": _row(
            "the application keeps a user signed in across requests, by a"
            " session cookie or a token",
            "Does the application keep users signed in across requests?",
        ),
        "reference-session-tokens": _row(
            "the application's sessions are opaque tokens looked up on the server",
            "Are the application's sessions stored on the server and referenced"
            " by an opaque token?",
            parent="sessions",
        ),
        "self-contained-tokens": _row(
            "the application issues or accepts self-contained tokens, such as"
            " JWTs or SAML assertions",
            "Does the application issue or accept signed tokens such as JWTs?",
        ),
        # --- OAuth and OpenID Connect ---
        "oauth": _row(
            "the application takes part in OAuth 2.0 or OpenID Connect",
            "Does the application use OAuth 2.0 or OpenID Connect?",
        ),
        "oauth-client": _row(
            "the application obtains tokens from an authorization server as an"
            " OAuth client",
            "Does the application obtain OAuth tokens from an authorization"
            " server, as a client?",
            parent="oauth",
        ),
        "oauth-resource-server": _row(
            "the application accepts OAuth access tokens on its API",
            "Does the application's API accept OAuth access tokens?",
            parent="oauth",
        ),
        "oauth-authorization-server": _row(
            "the application issues OAuth tokens as an authorization server",
            "Does the application issue OAuth tokens itself, as an authorization"
            " server?",
            parent="oauth",
        ),
        "oidc": _row(
            "the application uses OpenID Connect for sign-in",
            "Does the application use OpenID Connect for sign-in?",
            parent="oauth",
        ),
        # --- Authorization ---
        "restricted-access": _row(
            "the application limits some functions or data to particular users"
            " or callers",
            "Does the application limit any function or data to particular"
            " users or callers?",
        ),
        "multi-tenancy": _row(
            "the application serves several tenants whose data must stay apart",
            "Does the application serve several customer organizations"
            " (tenants) from one deployment?",
        ),
        "administrative-interface": _row(
            "the application has an interface for administrators",
            "Does the application have an administration interface?",
        ),
        # --- Data and cryptography ---
        "sensitive-data": _row(
            "the application handles personal, financial, health or other"
            " sensitive data",
            "Does the application handle personal, financial, health or other"
            " sensitive data?",
        ),
        "encryption": _row(
            "the application encrypts data itself, beyond the transport",
            "Does the application encrypt data itself, apart from TLS?",
        ),
        "internal-services": _row(
            "the application's backend is split into components that talk over"
            " a network, such as services, middleware or a database server",
            "Is the application's backend made of separate components that talk"
            " over a network?",
        ),
        # --- WebRTC ---
        "webrtc": _row(
            "the application uses WebRTC for real-time media or data",
            "Does the application use WebRTC for calls, video or real-time data?",
        ),
        "turn-server": _row(
            "the application operates a TURN relay server",
            "Does the application operate a TURN server?",
            parent="webrtc",
        ),
        "media-server": _row(
            "the application operates a WebRTC media server",
            "Does the application operate a media server for WebRTC?",
            parent="webrtc",
        ),
        "media-recording": _row(
            "the application's media server records audio or video",
            "Does the application record WebRTC audio or video?",
            parent="media-server",
        ),
        "signaling-server": _row(
            "the application operates a WebRTC signaling server",
            "Does the application operate a WebRTC signaling server?",
            parent="webrtc",
        ),
    }
)


@dataclass(frozen=True)
class CapabilityFact:
    """What one job knows about one capability, and what says so.

    ``evidence`` holds the quotes that stated the state. A state that a parent
    or a child implies carries that capability's quotes, and ``derived_from``
    names it.
    """

    key: str
    state: Presence
    evidence: tuple[str, ...] = ()
    derived_from: str = ""


@dataclass(frozen=True)
class Decision:
    """Whether one unit applies, and the facts that decided it or would.

    ``deciding`` holds the facts that settled an ``applicable`` or a
    ``not-applicable``, and is empty for an ``always`` unit. ``missing`` holds
    the unknown capability keys that would settle an ``unknown``, and is empty
    otherwise.
    """

    state: Applicability
    deciding: tuple[CapabilityFact, ...] = ()
    missing: tuple[str, ...] = ()


def lineage(key: str) -> Iterator[str]:
    """The key's ancestors, nearest first."""
    parent = CAPABILITIES[key].parent
    while parent:
        yield parent
        parent = CAPABILITIES[parent].parent


def resolve(known: Mapping[str, CapabilityFact]) -> Mapping[str, CapabilityFact]:
    """Every capability's fact, with what the parent links imply filled in.

    A key ``known`` leaves out is ``unknown``. An absent ancestor makes an
    unknown key absent, and a present descendant makes an unknown key present.
    Where the two directions disagree, or a stated key disagrees with what its
    parent implies, the key stays as ``known`` holds it: a conflict is for a
    person to settle, and this rule does not pick a side.
    """
    resolved: dict[str, CapabilityFact] = {}
    for key in CAPABILITIES:
        fact = known.get(key, CapabilityFact(key, "unknown"))
        if fact.state != "unknown":
            resolved[key] = fact
            continue
        absent = [
            ancestor
            for ancestor in lineage(key)
            if ancestor in known and known[ancestor].state == "absent"
        ]
        present = [
            other
            for other, found in known.items()
            if found.state == "present" and key in lineage(other)
        ]
        if absent and not present:
            source = known[absent[0]]
            fact = CapabilityFact(key, "absent", source.evidence, source.key)
        elif present and not absent:
            source = known[present[0]]
            fact = CapabilityFact(key, "present", source.evidence, source.key)
        resolved[key] = fact
    return MappingProxyType(resolved)


@dataclass(frozen=True)
class CapabilityNeed:
    """What one unknown capability's answer could settle for a framework.

    ``units`` counts the unknown decisions it, or a descendant of it, would
    settle. ``band`` is the order of the most important of them, as the
    framework ranks its units, the higher first.
    """

    units: int
    band: int


def open_capability_needs(
    decisions: Mapping[str, Decision], band: Callable[[str], int]
) -> dict[str, CapabilityNeed]:
    """How many unknown decisions each capability, or a descendant of it, would
    settle, and the band of the most important one.

    ``decisions`` maps each unit to its decision, and ``band`` gives a unit's
    band. A decision counts once for a capability, however many of its
    missing keys sit under it.
    """
    units: dict[str, int] = {}
    bands: dict[str, int] = {}
    for unit, decision in decisions.items():
        credited = {
            key for missing in decision.missing for key in (missing, *lineage(missing))
        }
        for key in credited:
            units[key] = units.get(key, 0) + 1
            bands[key] = max(bands.get(key, band(unit)), band(unit))
    return {key: CapabilityNeed(units=units[key], band=bands[key]) for key in units}


def _union(decisions: Sequence[Decision], field: str) -> tuple:
    seen: dict = {}
    for decision in decisions:
        for item in getattr(decision, field):
            seen.setdefault(item, None)
    return tuple(seen)


def evaluate(expression: Expression, facts: Mapping[str, CapabilityFact]) -> Decision:
    """Whether a unit with this applicability expression applies, in three values.

    ``facts`` is :func:`resolve`'s answer. A key it lacks is ``unknown``.

    ``all`` is not applicable when any term is not applicable, applicable when
    every term is, and unknown otherwise. ``any`` is the mirror. An unknown term
    is never read as false, so silence cannot rule a unit out.
    """
    if isinstance(expression, str):
        if expression == ALWAYS:
            return Decision("applicable")
        fact = facts.get(expression, CapabilityFact(expression, "unknown"))
        if fact.state == "present":
            return Decision("applicable", deciding=(fact,))
        if fact.state == "absent":
            return Decision("not-applicable", deciding=(fact,))
        return Decision("unknown", missing=(expression,))
    ((operator, terms),) = expression.items()
    decisions = [evaluate(term, facts) for term in terms]
    settles: Applicability = "not-applicable" if operator == "all" else "applicable"
    holds: Applicability = "applicable" if operator == "all" else "not-applicable"
    deciders = [decision for decision in decisions if decision.state == settles]
    if deciders:
        return Decision(settles, deciding=_union(deciders, "deciding"))
    if all(decision.state == holds for decision in decisions):
        return Decision(holds, deciding=_union(decisions, "deciding"))
    open_terms = [decision for decision in decisions if decision.state == "unknown"]
    return Decision("unknown", missing=_union(open_terms, "missing"))


def expression_keys(expression: Expression) -> tuple[str, ...]:
    """Every capability key an expression reads, in order, once each."""
    if isinstance(expression, str):
        return () if expression == ALWAYS else (expression,)
    ((_, terms),) = expression.items()
    return tuple(dict.fromkeys(key for term in terms for key in expression_keys(term)))


def expression_issues(expression: object) -> list[str]:
    """Everything that makes ``expression`` illegal, as messages."""
    if isinstance(expression, str):
        if expression == ALWAYS or expression in CAPABILITIES:
            return []
        return [f"{expression!r} is not a capability"]
    if not isinstance(expression, Mapping) or len(expression) != 1:
        return [f"{expression!r} is not one of always, a key, all or any"]
    ((operator, terms),) = expression.items()
    if operator not in ("all", "any"):
        return [f"{operator!r} is not all or any"]
    if not isinstance(terms, list) or len(terms) < 2:
        return [f"{operator} needs a list of two or more terms"]
    if ALWAYS in terms:
        return [f"{operator} holds always, which says nothing inside it"]
    return [issue for term in terms for issue in expression_issues(term)]
