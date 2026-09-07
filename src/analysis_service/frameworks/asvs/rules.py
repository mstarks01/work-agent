"""ASVS's applicability rules, and the tier-0 test the standard never states.

These rules are this repository's own, and all of them are. ASVS publishes a
requirement's chapter, section, identifier, description and level, and nothing
else. There is no applies-when field, no tag and no technology list, and the CWE
and NIST mappings of 4.x are gone in 5.0. Nothing therefore selects a
requirement for a system unless this repository writes it.

Most rules are presence tests. The #160 research derived 16 predicates across
the 70 level 1 requirements, and all 16 ask whether the application has a thing:
a browser frontend, cookies, a database, OAuth, a file upload, a session. Six
more were added afterwards, to reach the six chapters that had none, and they
ask the same shape of question. Five more, in ``STRUCTURAL_RULES``, read what the
corpus's own reference claims read: a stated credential on one attribute, a
classification, a write with no record named anywhere, a crossing from an
external entity, and a channel with no stated protection. Those are the leads a
term cannot raise.

Each rule reads free text by string match, because no attribute in the **System
Model** is a closed enum an ASVS predicate can test. #162 ruled that controls
stay string attributes, so this is the mechanism the model offers.

A **Candidate** is a lead rather than a gate. A lane agent still analyses its
chapter when no rule fires. Rules are authored for the level 1 requirements
first, so a requirement at level 2 or 3 with no rule reaches its lane agent
without a candidate. That is a weaker lead rather than an absent one.

Every lane carries at least one rule, and that is load-bearing beyond the lead.
Retrieval is keyed by fired rule, so a lane with no rule received no reference
note and no worked case either, whatever the knowledge tables held. The six
chapters that had no rule were therefore the six the corpus could not reach.

A term is chosen against what a submitter writes, and checked for what it also
matches. #189 found the OAuth terms were product names where submitters write
"SSO". The opposite failure is as easy: bare ``log`` matches ``login``, bare
``audit`` matches a food safety audit, and bare ``build`` matches building a
weekly rota. Where a single word is ambiguous, the term here is the phrase.

This is a term table rather than 17 functions. STRIDE writes one function per
rule, because each reads a different structure. Every rule here runs one matcher
over one attribute list, so 17 copies of that matcher would be 17 places for it
to drift. What a maintainer edits is the term list. The matcher is written once
below, and the table is the whole of what is authored.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from analysis_service.analysis import (
    TEXT_ATTRIBUTES,
    control_state,
    is_unverified,
    matches_term,
    names_term,
    states_a_protocol,
)
from analysis_service.candidates import Match, Rule, clip_fact
from analysis_service.frameworks import PreconditionResult
from analysis_service.frameworks.asvs.catalog import requirements_for
from analysis_service.system_model import SystemModel

__all__ = [
    "PRESENCE_TESTS",
    "REQUIREMENT_TESTS",
    "RULES",
    "STRUCTURAL_RULES",
    "WEB_PROTOCOL_TERMS",
    "asvs_precondition",
    "ruled_out_requirements",
]


@dataclass(frozen=True)
class PresenceTest:
    """One predicate: the lane it leads, and the terms that answer it.

    ``attributes`` narrows what the terms are matched against. A predicate about
    a wire protocol reads ``protocol`` and nothing else, so a process *named*
    "websocket gateway" does not answer a question about a flow.
    """

    predicate: str
    lane: str
    question: str
    terms: tuple[str, ...]
    attributes: tuple[str, ...] = TEXT_ATTRIBUTES
    #: Whether this test firing nowhere rules its whole chapter out (#443).
    #: True only where every requirement of the chapter presupposes the thing
    #: the terms name — a file upload, a self-contained token, an OAuth flow, a
    #: peer connection — so that its absence from the model is the answer.
    #: False where a silent model may still have the thing: most systems
    #: authenticate a caller whether or not the submitter wrote "login".
    decides_chapter: bool = False

    @property
    def rule_id(self) -> str:
        """This test's rule ID, which carries its lane like STRIDE's do."""
        return f"{self.lane}-{self.predicate}"


def _hits(model: SystemModel, test: PresenceTest) -> Iterator[Match]:
    """Every element whose text answers this presence test, in model order.

    One match per element rather than one per term: a store named
    "PostgreSQL orders database" answers ``tech:database`` once, and the fact
    records which attribute and which term answered it.
    """
    for element in model.elements():
        for attribute in test.attributes:
            value = getattr(element, attribute, "")
            if not isinstance(value, str):
                continue
            lowered = value.lower()
            term = next(
                (term for term in test.terms if matches_term(term, lowered)), ""
            )
            if not term:
                continue
            yield (
                (element.id,),
                {"attribute": attribute, "value": clip_fact(value), "term": term},
            )
            break


def _rule_of(test: PresenceTest) -> Rule:
    """One presence test as the neutral :class:`~analysis_service.candidates.Rule`."""

    def find(model: SystemModel) -> Iterator[Match]:
        return _hits(model, test)

    return Rule(
        rule_id=test.rule_id,
        lane=test.lane,
        question=test.question,
        find=find,
    )


# 23 rules over 17 lanes. The first 17 are the #160 research's 16 predicates —
# ``tech:browser frontend`` appears twice because it leads requirements in two
# chapters, and a rule belongs to exactly one lane, so the two carry the same
# terms and put a different question. The last six close the chapters that had
# no rule, and so no retrieval either.
PRESENCE_TESTS: tuple[PresenceTest, ...] = (
    PresenceTest(
        predicate="database",
        lane="encoding-and-sanitization",
        question=(
            "This system stores data in a query-driven store. Does the"
            " application build queries from input, and how?"
        ),
        terms=(
            "sql",
            "postgres",
            "mysql",
            "mariadb",
            "sqlite",
            "oracle",
            "mongo",
            "dynamodb",
            "cassandra",
            "bigquery",
            "redshift",
            "database",
        ),
    ),
    PresenceTest(
        predicate="rich-text-input",
        lane="encoding-and-sanitization",
        question=(
            "This system accepts authored text from a user. Does anything"
            " sanitize it before it is rendered or stored?"
        ),
        terms=(
            "rich text",
            "wysiwyg",
            "markdown",
            "user-generated",
            "user generated",
            "comment",
            "html editor",
        ),
    ),
    PresenceTest(
        predicate="xml-parser",
        lane="encoding-and-sanitization",
        question=(
            "This system parses XML. Are external entities and schema"
            " references disabled?"
        ),
        terms=("xml", "soap", "xslt", "xsd", "svg"),
    ),
    PresenceTest(
        predicate="client-side-code",
        lane="validation-and-business-logic",
        question=(
            "This system runs code on an untrusted side. Which side enforces"
            " each validation rule?"
        ),
        terms=(
            "javascript",
            "browser",
            "single-page",
            "single page",
            "client-side",
            "react",
            "angular",
            "vue",
            "mobile app",
        ),
    ),
    PresenceTest(
        predicate="multi-step-flow",
        lane="validation-and-business-logic",
        question=(
            "This system runs a business flow in stages. Can a stage be"
            " reached out of order or replayed?"
        ),
        terms=(
            "checkout",
            "wizard",
            "multi-step",
            "multi step",
            "onboarding",
            "workflow",
        ),
    ),
    PresenceTest(
        predicate="browser-frontend",
        lane="web-frontend-security",
        question=(
            "This system serves a browser. Which response headers, cookie"
            " attributes and origin rules does it set?"
        ),
        terms=(
            "browser",
            "web ui",
            "web app",
            "webapp",
            "web frontend",
            "web portal",
            "html",
            "single-page",
            "single page",
            "react",
            "angular",
            "vue",
        ),
    ),
    PresenceTest(
        predicate="cookies",
        lane="web-frontend-security",
        question=("This system sets cookies. Which attributes does each one carry?"),
        terms=("cookie", "set-cookie"),
    ),
    PresenceTest(
        predicate="cors",
        lane="web-frontend-security",
        question=(
            "This system answers a cross-origin caller. Which origins does it"
            " allow, and does it allow credentials?"
        ),
        terms=("cors", "cross-origin", "cross origin", "access-control-allow"),
    ),
    PresenceTest(
        predicate="websocket",
        lane="api-and-web-service",
        question=(
            "This system carries a WebSocket. Is the handshake authenticated"
            " and the transport protected?"
        ),
        terms=("websocket", "ws://", "wss://", "socket.io"),
        attributes=("protocol", "technology", "description"),
    ),
    PresenceTest(
        predicate="file-upload",
        decides_chapter=True,
        lane="file-handling",
        question=(
            "This system accepts an uploaded file. What limits its size, its"
            " type and where it lands?"
        ),
        terms=("upload", "attachment", "multipart", "artifact", "registry"),
    ),
    PresenceTest(
        predicate="authentication",
        lane="authentication",
        question=(
            "This system authenticates a caller. Which factors does it accept"
            " and what documents them?"
        ),
        terms=(
            "authenticat",
            "login",
            "log in",
            "sign-in",
            "sign in",
            "sso",
            "credential",
            "mfa",
        ),
    ),
    PresenceTest(
        predicate="password-auth",
        lane="authentication",
        question=(
            "This system accepts a password. What rules govern its length,"
            " its storage and its rotation?"
        ),
        terms=("password", "passphrase", "basic auth"),
    ),
    PresenceTest(
        predicate="sessions",
        lane="session-management",
        question=(
            "This system holds a session. How is its token generated, bound"
            " and terminated?"
        ),
        terms=("session", "cookie", "jsessionid"),
    ),
    PresenceTest(
        predicate="self-contained-tokens",
        decides_chapter=True,
        lane="self-contained-tokens",
        question=(
            "This system carries a self-contained token. Which algorithms and"
            " claims does the verifier accept?"
        ),
        # The last four name self-containment rather than a format, because a
        # submitter states the property long before they state the standard: a
        # SAML assertion is self-contained by construction, and "signed token"
        # says the verifier trusts the bytes. The bare word ``token`` is
        # deliberately absent — an opaque session or build token is looked up
        # server-side and is what this lane is not about. ``claims`` is absent
        # for the same reason from the other direction: in this corpus it
        # matches insurance claim data.
        terms=(
            "jwt",
            "json web token",
            "jws",
            "jwe",
            "bearer token",
            "id token",
            "assertion",
            "signed token",
            "stateless token",
            "self-contained",
        ),
    ),
    PresenceTest(
        predicate="oauth",
        decides_chapter=True,
        lane="oauth-and-oidc",
        question=(
            "This system uses OAuth or OIDC. Which grant, which redirect URIs"
            " and which client authentication?"
        ),
        # The federation terms carry the weight here. A submitter writes "company
        # SSO" and "identity provider"; the protocol acronyms and product names
        # appear only when someone already knows which standard they run. "SSO"
        # can mean SAML, which is not this chapter — accepted, because a
        # candidate is a lead the lane agent judges, ASVS declares no federation
        # lane closer to it, and the alternative is that a system whose whole
        # subject is federated sign-in gets no lead at all.
        terms=(
            "oauth",
            "oidc",
            "openid",
            "authorization server",
            "keycloak",
            "auth0",
            "okta",
            "sso",
            "single sign-on",
            "single sign on",
            "identity provider",
            "identity broker",
            "federated",
            "federation",
        ),
    ),
    PresenceTest(
        predicate="encryption",
        lane="cryptography",
        question=(
            "This system encrypts something. Which algorithm, which mode and"
            " which key management?"
        ),
        terms=("encrypt", "aes", "cipher", "kms", "hsm", "tls"),
    ),
    PresenceTest(
        predicate="browser-frontend",
        lane="data-protection",
        question=(
            "This system serves a browser. Does anything stop sensitive data"
            " being cached or stored on the client?"
        ),
        terms=(
            "browser",
            "web ui",
            "web app",
            "webapp",
            "web frontend",
            "web portal",
            "html",
            "single-page",
            "single page",
            "react",
            "angular",
            "vue",
        ),
    ),
    PresenceTest(
        predicate="privileged-role",
        lane="authorization",
        question=(
            "This system distinguishes one kind of caller from another. What"
            " decides which functions and which records each may reach?"
        ),
        terms=(
            "admin",
            "administrator",
            "role",
            "permission",
            "rbac",
            "entitlement",
            "tenant",
            "privileged",
            "moderator",
            "back-office",
            "superuser",
        ),
    ),
    PresenceTest(
        predicate="transport",
        lane="secure-communication",
        question=(
            "This system speaks over a network. What protects the channel, and"
            " which party's certificate is verified?"
        ),
        # The wire attributes and nothing else, on the rule this table already
        # states: a process *named* "tls terminator" is not a flow that speaks
        # TLS. Reading the free text here would fire on every mention of a
        # certificate as a business document -- the corpus carries insurance
        # certificates -- and the chapter is about the channel.
        terms=("http", "tls", "ssl", "mtls", "certificate", "wss"),
        attributes=("protocol", "encryption_in_transit"),
    ),
    PresenceTest(
        predicate="secret-material",
        lane="configuration",
        question=(
            "This system holds configuration or a secret. Where does it live,"
            " who can read it, and how is it rotated?"
        ),
        # Not bare "config" or "deploy". Both are ordinary English in a system
        # description -- the corpus has a "deployed sensor" and "device
        # configuration and diagnostics" -- and neither is this chapter's
        # subject. The terms below name the thing itself.
        terms=(
            "secret",
            "credential",
            "environment variable",
            "env var",
            "vault",
            "feature flag",
            "api key",
            "access key",
            "iam role",
            "service account",
            "application config",
        ),
    ),
    PresenceTest(
        predicate="third-party-component",
        lane="secure-coding-and-architecture",
        question=(
            "This system runs code or trusts a component it did not author."
            " What fixes the version, and what checks it before it runs?"
        ),
        # Not bare "build": the corpus "builds the weekly rota for a store",
        # which is a business verb and not a release pipeline.
        terms=(
            "dependency",
            "dependencies",
            "lockfile",
            "package registry",
            "container image",
            "third-party",
            "third party",
            "open source",
            "sbom",
            "artifact",
            "ci/cd",
            "build pipeline",
            "npm",
            "pypi",
            "maven",
        ),
    ),
    PresenceTest(
        predicate="log-or-audit-trail",
        lane="security-logging-and-error-handling",
        question=(
            "This system records what happened. What reaches the log, what is"
            " kept out of it, and who is alerted?"
        ),
        # Not bare "log", which is a substring of "login", and not bare "audit",
        # which the corpus uses for food safety audits.
        terms=(
            "logging",
            "logs",
            "log file",
            "log record",
            "audit log",
            "audit trail",
            "siem",
            "alerting",
            "telemetry",
            "stack trace",
            "error handling",
        ),
    ),
    PresenceTest(
        predicate="real-time-media",
        decides_chapter=True,
        lane="webrtc",
        question=(
            "This system carries real-time media or a peer connection. What"
            " authenticates the signalling, and what encrypts the media?"
        ),
        # Multi-word on purpose. "turn" and "stun" are ordinary words; the
        # server roles they name are not.
        terms=(
            "webrtc",
            "stun server",
            "turn server",
            "sdp",
            "peer connection",
            "peer-to-peer",
            "video call",
            "voice call",
            "screen share",
            "data channel",
        ),
    ),
)


# --- Leads the term table cannot raise ---------------------------------------
#
# #430 measured the term table against the corpus: 43 of 99 reference claims
# sat in a lane that raised no candidate, because the references are written
# from the model's structure and the terms ask whether a technology exists.
# These read the structure the references read. Two are narrowed term tests on
# one attribute; two read the graph the way STRIDE's rules do.

SHARED_ACCOUNT_TEST = PresenceTest(
    predicate="shared-account",
    lane="authorization",
    question=(
        "One credential reaches this store for every caller. What restricts"
        " which records each caller may reach through it?"
    ),
    terms=(
        "shared",
        "single account",
        "single credential",
        "single user",
        "same account",
        "one account",
        "full read",
        "read/write",
        "read-write",
        "read and write",
        "all tables",
    ),
    attributes=("authentication",),
)

CLASSIFIED_STORE_TEST = PresenceTest(
    predicate="classified-store",
    lane="data-protection",
    question=(
        "This store carries a stated classification. What protection does"
        " that classification require, and what states it is in place?"
    ),
    terms=("confidential", "restricted", "secret", "sensitive", "regulated", "pii"),
    attributes=("data_classification",),
)

#: Store asset tags whose writes the standard expects a record of.
AUDITED_ASSET_TAGS = frozenset({"financial", "business-critical-data", "pii", "health"})

#: What a store or process says when it holds a record of what happened.
AUDIT_TERMS = (
    "log$",
    "logs$",
    "logging",
    "logged",
    "audit",
    "receipt",
    "journal",
    "history",
    "event",
)


def _mentions_a_record(model: SystemModel) -> bool:
    """Whether any element's text says it holds a log, an audit trail or a receipt."""
    return any(
        matches_term(term, f"{element.name} {element.description}".lower())
        for element in model.elements()
        for term in AUDIT_TERMS
    )


def _write_with_no_record(model: SystemModel) -> Iterator[Match]:
    """A process writing an audited asset into a store, in a model naming no record.

    The V16 finding the corpus writes is that the record names the service and
    not the actor, or that there is no record at all. A term test cannot raise
    it: a submitter writes "the receipt records that the order service wrote
    it", never the word "log".
    """
    stores = {store.id: store for store in model.data_stores}
    mentions = _mentions_a_record(model)
    for flow in model.data_flows:
        store = stores.get(flow.destination)
        if store is None or not AUDITED_ASSET_TAGS & set(store.assets):
            continue
        yield (
            (flow.source, store.id, flow.id),
            {
                "assets": ", ".join(sorted(AUDITED_ASSET_TAGS & set(store.assets))),
                "authentication": clip_fact(flow.authentication),
                "record_named_anywhere": mentions,
            },
        )


def _crossing_from_an_entity(model: SystemModel) -> Iterator[Match]:
    """A boundary crossing whose source is an external entity.

    The V2 requirements ask which side enforces validation of what a caller
    submits, and the crossing from a zone the system does not control is the
    fact that makes them apply.
    """
    entities = {entity.id: entity for entity in model.external_entities}
    flows = {flow.id: flow for flow in model.data_flows}
    for crossing in model.boundary_crossings():
        flow = flows[crossing.flow_id]
        entity = entities.get(flow.source)
        if entity is None:
            continue
        yield (
            (flow.id, entity.id, flow.destination),
            {
                "source_kind": entity.kind,
                "source_zone": crossing.source_zone,
                "destination_zone": crossing.destination_zone,
                "data_description": clip_fact(flow.data_description),
            },
        )


def _unverified_transit(model: SystemModel) -> Iterator[Match]:
    """A flow whose transport protection the input never settled or ruled out.

    The transport term test reads ``protocol``, so a flow that says gRPC and
    nothing about encryption raises no lead in the chapter that asks about
    exactly that.
    """
    for flow in model.data_flows:
        if not is_unverified(flow.encryption_in_transit):
            continue
        yield (
            (flow.id, flow.source, flow.destination),
            {
                "protocol": clip_fact(flow.protocol),
                "encryption_in_transit": clip_fact(flow.encryption_in_transit),
                "encryption_state": control_state(flow.encryption_in_transit),
            },
        )


STRUCTURAL_RULES: tuple[Rule, ...] = (
    Rule(
        rule_id="secure-communication-unverified-transit",
        lane="secure-communication",
        question=(
            "Nothing states what protects this channel. Which requirement of"
            " the chapter does the input leave open, and which does it settle?"
        ),
        find=_unverified_transit,
    ),
    _rule_of(SHARED_ACCOUNT_TEST),
    _rule_of(CLASSIFIED_STORE_TEST),
    Rule(
        rule_id="security-logging-and-error-handling-write-with-no-record",
        lane="security-logging-and-error-handling",
        question=(
            "This process writes an asset the standard expects a record of."
            " What records the write, and does the record name the actor or"
            " only the service?"
        ),
        find=_write_with_no_record,
    ),
    Rule(
        rule_id="validation-and-business-logic-crossing-from-an-entity",
        lane="validation-and-business-logic",
        question=(
            "A party outside the system's control submits this flow across a"
            " boundary. Which side validates what it carries, and what says so?"
        ),
        find=_crossing_from_an_entity,
    ),
)


#: Requirements whose own text names a technology, against the words a
#: submitter writes for it (#455). Where none of the words appears at the
#: start of a word anywhere in the model, the requirement is ruled out in code
#: with the terms in the reason, the way a deciding chapter test rules its
#: chapter out. Only requirements that presuppose the thing are here: V1.2.2
#: (URL building), V1.2.5 (OS commands) and V1.3.6 (outbound fetches) ask
#: about what an application does, not what it names, and stay with the lane.
#: Checked at import against the catalog, so a retired identifier fails closed.
REQUIREMENT_TESTS: dict[str, tuple[str, ...]] = {
    "V1.2.6": ("ldap", "active directory", "directory service"),
    "V1.2.7": ("xpath", "xml"),
    "V1.2.8": ("latex", "tex$"),
    "V1.2.10": ("csv", "spreadsheet", "excel", "export"),
    "V1.3.1": ("wysiwyg", "rich text", "html editor", "markdown"),
    "V1.3.5": ("markdown", "css", "xsl", "template"),
    "V1.3.7": ("template",),
    "V1.3.8": ("jndi", "java$"),
    "V1.3.9": ("memcache",),
    "V1.3.11": ("mail", "smtp", "imap", "email"),
    "V1.5.1": ("xml", "soap", "xslt", "xsd", "svg"),
    "V4.3.1": ("graphql",),
    "V4.3.2": ("graphql",),
    "V4.4.1": ("websocket", "ws://", "wss://", "socket.io"),
    "V4.4.2": ("websocket", "ws://", "wss://", "socket.io"),
    "V4.4.3": ("websocket", "ws://", "wss://", "socket.io"),
    "V4.4.4": ("websocket", "ws://", "wss://", "socket.io"),
    **dict.fromkeys(
        ("V6.5.1", "V6.5.2", "V6.5.3", "V6.5.4", "V6.5.5"),
        (
            "lookup secret",
            "totp",
            "one-time",
            "otp",
            "out-of-band",
            "authenticator app",
            "backup code",
            "recovery code",
            "mfa",
            "second factor",
            "two-factor",
            "2fa",
        ),
    ),
    **dict.fromkeys(
        ("V6.6.1", "V6.6.2", "V6.6.3"),
        ("sms", "phone", "pstn", "out-of-band", "otp", "one-time", "push notification"),
    ),
    **dict.fromkeys(
        ("V6.8.1", "V6.8.2", "V6.8.3", "V6.8.4", "V7.1.3", "V7.6.1", "V7.6.2"),
        (
            "identity provider",
            "idp",
            "saml",
            "federat",
            "sso",
            "single sign",
            "oidc",
            "openid",
            "relying party",
        ),
    ),
}


def ruled_out_requirements(model: SystemModel, level: int, lane: str) -> dict[str, str]:
    """The requirements of ``lane`` ruled out because the model names nothing they need.

    Two readings, one answer. A deciding chapter test that fired nowhere rules
    every requirement of the chapter out; a requirement in
    :data:`REQUIREMENT_TESTS` whose own terms appear nowhere rules itself out.
    Each is keyed by the standard's own identifier against the reason a reader
    gets.
    """
    ruled_out: dict[str, str] = {}
    for test in PRESENCE_TESTS:
        if test.lane != lane or not test.decides_chapter:
            continue
        if any(True for _ in _hits(model, test)):
            continue
        reason = (
            f"no element of this system names {test.predicate.replace('-', ' ')}"
            f" ({', '.join(test.terms[:4])}, ...), and every requirement of this"
            f" chapter presupposes one; ruled out in code by {test.rule_id}"
        )
        for requirement in requirements_for(level, lane):
            ruled_out[requirement.id] = reason
    for requirement in requirements_for(level, lane):
        terms = REQUIREMENT_TESTS.get(requirement.id)
        if (
            requirement.id in ruled_out
            or terms is None
            or any(names_term(model, term) for term in terms)
        ):
            continue
        ruled_out[requirement.id] = (
            f"no element of this system names what {requirement.id} presupposes"
            f" ({', '.join(terms[:4])}{', ...' if len(terms) > 4 else ''});"
            " ruled out in code"
        )
    return ruled_out


def _requirement_test_issues() -> list[str]:
    """Every key of :data:`REQUIREMENT_TESTS` the catalog does not publish."""
    published = {requirement.id for requirement in requirements_for(3)}
    return [key for key in REQUIREMENT_TESTS if key not in published]


if _requirement_test_issues():
    raise ValueError(
        "REQUIREMENT_TESTS names requirements the catalog does not publish:"
        f" {_requirement_test_issues()}"
    )


RULES: tuple[Rule, ...] = (
    *(_rule_of(test) for test in PRESENCE_TESTS),
    *STRUCTURAL_RULES,
)


#: What a **Data Flow**'s ``protocol`` says when the flow carries the web. ASVS
#: scopes itself to "web applications and services", so an API over HTTP is in
#: scope and a message bus is not.
WEB_PROTOCOL_TERMS: tuple[str, ...] = (
    "http",
    "https",
    "rest",
    "graphql",
    "soap",
    "grpc",
    "websocket",
    "ws://",
    "wss://",
)


def asvs_precondition(model: SystemModel) -> PreconditionResult:
    """Is the target a web application or service?

    **The tier-0 test the standard never states as a requirement.** ASVS defines
    security requirements for "web applications and services" in its first
    sentence, and no requirement asks whether the target is one: the standard
    assumes an operator settled that before opening it. STRIDE's precondition is
    total, so ASVS is the first framework here that can answer no.

    **The read is what the processes say they present**, not what the flows say
    they carry. Those are different facts, and reading the second for the first
    is what
    [#219](https://github.com/mstarks01/work-agent/issues/219) found: six corpus
    cases answered ``undecidable`` — among them a process named ``scheduling web
    app`` and another named ``supplier portal`` — because every flow's
    ``protocol`` was ``unknown``. The transport was genuinely unstated, and
    ``unknown`` was the correct value for it. The applicability question was
    simply not a question about transport.

    A stated protocol still satisfies, because a flow that says HTTPS says the
    same thing by another route. **It can no longer refuse on its own**, and it
    can no longer hold the answer open: a model whose every process states
    ``non-web`` has answered, whatever its flows leave unsaid.

    * ``satisfied`` — a process presents a web interface, or a flow speaks a web
      protocol.
    * ``undecidable`` — nothing says web and something never said. The input
      never settled it, and submitting more about the system does.
    * ``refuted`` — every process states a non-web interface, or (for a model
      carrying no processes) every flow states a non-web protocol.

    A model with no processes and no flows is ``undecidable`` on the same terms:
    nothing said, rather than nothing there.

    **Anything that never said is enough to hold the answer open**, and that is
    the whole point of carrying three states rather than two. Reading silence as
    a refusal would tell an operator "do not name ASVS for this system" when the
    truth is "your input did not say" — and those two have different remedies,
    which is the distinction this repo refuses to collapse anywhere else.
    """
    kinds = [process.interface_kind for process in model.processes]
    speaks_web = any(
        term in flow.protocol.lower()
        for flow in model.data_flows
        for term in WEB_PROTOCOL_TERMS
    )
    if "web" in kinds or speaks_web:
        return "satisfied"

    # No process to read: fall back to the flows, which is the whole of what a
    # model carrying only stores and entities can be asked.
    if not kinds:
        silent = any(not states_a_protocol(flow.protocol) for flow in model.data_flows)
        return "undecidable" if silent or not model.data_flows else "refuted"

    return "undecidable" if "unknown" in kinds else "refuted"
