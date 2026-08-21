"""ASVS's applicability rules, and the tier-0 test the standard never states.

**These rules are this repo's own, and all of them.** ASVS publishes a
requirement's chapter, section, identifier, description and level, and nothing
else: no applies-when field, no tag and no technology list. The CWE and NIST
mappings of 4.x are gone in 5.0. So nothing selects a requirement for a system
unless this repo writes it.

**Every rule is a presence test.** The #160 research derived 16 predicates across
the 70 level 1 requirements, and all 16 ask whether the application *has* a thing
— a browser frontend, cookies, a database, OAuth, a file upload, a session. Six
more were added afterwards to reach the six chapters that had none, and they ask
the same shape of question. Not one reads an element type, a trust zone, a
boundary crossing or a count. That is the sharpest contrast with STRIDE's 11
rules, which read exactly those things.

**Each rule reads free text by string match**, because no attribute in the
**System Model** is a closed enum an ASVS predicate can test. #162 ruled that
controls stay string attributes, so this is the mechanism the model offers.

A **Candidate** is a lead rather than a gate: a lane agent still analyses its
chapter when no rule fires. Rules are authored for the level 1 requirements
first, so a requirement at level 2 or 3 with no rule reaches its lane agent
without a candidate, which is a weaker lead and not an absent one.

**Every lane carries at least one rule, and that is load-bearing beyond the
lead.** Retrieval is keyed by *fired rule*, so a lane with no rule received no
reference note and no worked case either, whatever the knowledge tables held.
The six chapters that had no rule were therefore the six the corpus could not
reach.

**A term is chosen against what a submitter writes, and checked for what it
also matches.** #189 found the OAuth terms were product names where submitters
write "SSO". The opposite failure is as easy: bare ``log`` matches ``login``,
bare ``audit`` matches a food safety audit, and bare ``build`` matches building
a weekly rota. Where a single word is ambiguous the term here is the phrase.

**A term table rather than 17 functions.** STRIDE writes one function per rule
because each reads a different structure. Every rule here runs one matcher over
one attribute list, so 17 copies of that matcher would be 17 places for it to
drift, and what a maintainer edits is the term list. The matcher is written once
below and the table is the whole of what is authored.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from stride_service.analysis import control_state
from stride_service.candidates import Match, Rule, clip_fact
from stride_service.frameworks import PreconditionResult
from stride_service.system_model import SystemModel

__all__ = ["PRESENCE_TESTS", "RULES", "WEB_PROTOCOL_TERMS", "asvs_precondition"]

#: The free-text attributes a presence test may read, per element type. Every one
#: is a ``str`` the submitter authored, which is why a rule here matches a term
#: rather than looking a value up.
_TEXT_ATTRIBUTES: tuple[str, ...] = (
    "name",
    "description",
    "notes",
    "technology",
    "protocol",
    "authentication",
    "data_description",
    "data_classification",
    "encryption_at_rest",
    "encryption_in_transit",
)


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
    attributes: tuple[str, ...] = _TEXT_ATTRIBUTES

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
            term = next((term for term in test.terms if term in lowered), "")
            if not term:
                continue
            yield (
                (element.id,),
                {"attribute": attribute, "value": clip_fact(value), "term": term},
            )
            break


def _rule_of(test: PresenceTest) -> Rule:
    """One presence test as the neutral :class:`~stride_service.candidates.Rule`."""

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
        lane="file-handling",
        question=(
            "This system accepts an uploaded file. What limits its size, its"
            " type and where it lands?"
        ),
        terms=("upload", "attachment", "multipart"),
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

RULES: tuple[Rule, ...] = tuple(_rule_of(test) for test in PRESENCE_TESTS)


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


def _states_a_protocol(protocol: str) -> bool:
    """Whether this flow's ``protocol`` says anything at all about what it speaks.

    **Read through the service's own leading-token rule**, not a bare prefix
    test. :func:`~stride_service.analysis.control_state` is what every other
    reader of an attribute in this repo uses: it matches ``unknown`` or ``none``
    as a *word*, so ``"unknownish binary framing"`` is a stated protocol and
    ``"unknown; the team never said"`` is not. A prefix test read the first as
    silence.

    The empty string is silence too, and it has to be said separately because
    ``control_state`` calls it ``stated`` — correctly, since for a *control* an
    empty value is not evidence of anything. A protocol nobody filled in is the
    same fact as one nobody knew, and both are what ``undecidable`` is for.
    """
    return bool(protocol.strip()) and control_state(protocol) == "stated"


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
        silent = any(not _states_a_protocol(flow.protocol) for flow in model.data_flows)
        return "undecidable" if silent or not model.data_flows else "refuted"

    return "undecidable" if "unknown" in kinds else "refuted"
