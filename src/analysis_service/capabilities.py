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


def _row(
    meaning: str, question: str, parent: str = "", reviewed_by: str | None = None
) -> Capability:
    return Capability(
        meaning=meaning, question=question, parent=parent, reviewed_by=reviewed_by
    )


CAPABILITIES: Mapping[str, Capability] = MappingProxyType(
    {
        # --- Clients and interfaces ---
        "browser-frontend": _row(
            "people use the application through pages or scripts it serves to a"
            " web browser",
            "Do people use the application through a web browser?",
            reviewed_by="mstarks01",
        ),
        "browser-accessible-http": _row(
            "the application serves HTTP content that a web browser can open,"
            " including API and file responses with no frontend",
            "Does the application serve pages, scripts, files or HTTP responses"
            " that people can open in a web browser?",
            reviewed_by="mstarks01",
        ),
        "postmessage-receiver": _row(
            "the application's browser code receives messages from other documents"
            " or windows, such as through postMessage",
            "Does the application's browser code receive messages from other"
            " windows or frames, such as through postMessage?",
            reviewed_by="mstarks01",
        ),
        "external-browser-resources": _row(
            "the application's pages load scripts, styles or other resources"
            " hosted outside the application",
            "Do the application's pages load externally hosted scripts, styles or"
            " other resources, including resources delivered through a CDN?",
            reviewed_by="mstarks01",
        ),
        "native-client": _row(
            "people use the application through a mobile or desktop app that calls it",
            "Do people use the application through a mobile or desktop app?",
            reviewed_by="mstarks01",
        ),
        "cookies": _row(
            "the application sets cookies in its responses",
            "Does the application set cookies?",
            reviewed_by="mstarks01",
        ),
        "cors": _row(
            "the application lets browser scripts from another origin (scheme,"
            " host or port) read its responses through CORS",
            "Does the application allow browser scripts from a different origin"
            " (scheme, host or port) to read its responses through CORS?",
            reviewed_by="mstarks01",
        ),
        "websocket": _row(
            "the application holds WebSocket connections",
            "Does the application use WebSockets?",
            reviewed_by="mstarks01",
        ),
        "graphql": _row(
            "the application serves a GraphQL API",
            "Does the application serve a GraphQL API?",
            reviewed_by="mstarks01",
        ),
        "http-caching": _row(
            "HTTP caching, including an intermediary cache, can hold the"
            " application's responses",
            "Can an HTTP cache, such as a CDN or a proxy, store the application's"
            " responses?",
            reviewed_by="mstarks01",
        ),
        "sensitive-http-requests": _row(
            "the application's HTTP requests carry sensitive data, including"
            " credentials and session tokens",
            "Do HTTP requests to or from the application carry sensitive data,"
            " such as credentials or session tokens?",
            reviewed_by="mstarks01",
        ),
        # --- Code and runtimes ---
        "javascript-code": _row(
            "an in-scope component executes JavaScript, in a browser, a server,"
            " a native app or an embedded runtime",
            "Does any in-scope component execute JavaScript, including browser,"
            " server, native or embedded code?",
            reviewed_by="mstarks01",
        ),
        "memory-unsafe-code": _row(
            "part of the application is written in a language without memory"
            " safety, such as C or C++",
            "Is any part of the application written in C, C++ or another"
            " language without memory safety?",
            reviewed_by="mstarks01",
        ),
        "overflow-capable-arithmetic": _row(
            "the application performs arithmetic with bounded numeric types that"
            " can overflow or underflow, whether or not protective checks exist",
            "Does the application use bounded numeric arithmetic, such as"
            " fixed-width integers or floating-point numbers?",
            reviewed_by="mstarks01",
        ),
        "manually-managed-resources": _row(
            "the application allocates memory or other resources that its code"
            " must release explicitly",
            "Does the application's code allocate memory or other resources that"
            " it must release explicitly?",
            reviewed_by="mstarks01",
        ),
        # --- Interpreters and parsers the application feeds ---
        "database": _row(
            "the application queries a database with a query language (SQL,"
            " NoSQL, graph)",
            "Does the application query a database?",
            reviewed_by="mstarks01",
        ),
        "os-command": _row(
            "the application runs operating system commands or programs",
            "Does the application run operating system commands or external programs?",
            reviewed_by="mstarks01",
        ),
        "ldap": _row(
            "the application queries an LDAP directory",
            "Does the application query an LDAP directory?",
            reviewed_by="mstarks01",
        ),
        "xpath": _row(
            "the application runs XPath queries",
            "Does the application run XPath queries?",
            reviewed_by="mstarks01",
        ),
        "latex": _row(
            "the application renders LaTeX",
            "Does the application process LaTeX?",
            reviewed_by="mstarks01",
        ),
        "jndi": _row(
            "the application performs JNDI lookups",
            "Does the application perform JNDI lookups?",
            reviewed_by="mstarks01",
        ),
        "cache-service": _row(
            "the application stores data in a cache service such as memcached or Redis",
            "Does the application use a cache service such as memcached or Redis?",
            reviewed_by="mstarks01",
        ),
        "memcache": _row(
            "the application sends content to a service that speaks the memcache"
            " protocol",
            "Does the application send content to a memcache protocol service?",
            parent="cache-service",
            reviewed_by="mstarks01",
        ),
        "mail-sending": _row(
            "the application sends or reads email through a mail server",
            "Does the application send or read email?",
            reviewed_by="mstarks01",
        ),
        "untrusted-template-input": _row(
            "the application puts untrusted input into the construction or the"
            " evaluation of a template, by any template engine",
            "Is untrusted input incorporated into template construction or evaluation?",
            reviewed_by="mstarks01",
        ),
        "dynamic-regex": _row(
            "the application builds regular expressions from untrusted input,"
            " including data it receives from other systems",
            "Does the application construct regular expressions using untrusted"
            " input, including data received indirectly from other systems?",
            reviewed_by="mstarks01",
        ),
        "xml-parsing": _row(
            "the application parses XML, including SOAP and SVG",
            "Does the application parse XML, SOAP or SVG?",
            reviewed_by="mstarks01",
        ),
        "rich-text-input": _row(
            "the application accepts markup from its users or from other systems:"
            " HTML, SVG, Markdown, CSS, BBCode or templates",
            "Does the application accept formatted text or markup from users or"
            " other systems, such as HTML, Markdown or SVG?",
            reviewed_by="mstarks01",
        ),
        "untrusted-html": _row(
            "the application accepts HTML from an untrusted source",
            "Does the application accept HTML from users or other untrusted sources?",
            parent="rich-text-input",
            reviewed_by="mstarks01",
        ),
        "untrusted-svg": _row(
            "the application accepts SVG from an untrusted source",
            "Does the application accept SVG from users or other untrusted sources?",
            parent="rich-text-input",
            reviewed_by="mstarks01",
        ),
        "untrusted-scriptable-content": _row(
            "the application accepts untrusted content that can carry scripts or"
            " expressions, such as Markdown, CSS, XSL or BBCode",
            "Does the application accept Markdown, CSS, XSL, BBCode or similar"
            " content from users or other untrusted sources?",
            parent="rich-text-input",
            reviewed_by="mstarks01",
        ),
        "spreadsheet-export": _row(
            "the application exports data as CSV or spreadsheet files",
            "Does the application export data as CSV or spreadsheet files?",
            reviewed_by="mstarks01",
        ),
        # --- Calls to other services ---
        "outbound-http": _row(
            "the application's backend sends HTTP requests to other services",
            "Does the application's backend call other HTTP services or APIs?",
            reviewed_by="mstarks01",
        ),
        "http-request-construction": _row(
            "frontend or backend code builds HTTP requests to other services",
            "Does frontend or backend code construct HTTP requests to other services?",
            reviewed_by="mstarks01",
        ),
        "untrusted-outbound-destination": _row(
            "untrusted input, direct or indirect, shapes any part of a"
            " server-side call to another service, by HTTP or another scheme",
            "Can untrusted input influence any part of a server-side call to"
            " another service, such as its scheme, host, port or path?",
            reviewed_by="mstarks01",
        ),
        # --- Business behaviour ---
        "multi-step-flow": _row(
            "the application has a business flow of ordered steps, such as a"
            " checkout or a sign-up wizard",
            "Does the application have multi-step flows, such as a checkout or a"
            " wizard?",
            reviewed_by="mstarks01",
        ),
        "human-paced-business-flow": _row(
            "a business flow has a realistic minimum time for a person to"
            " complete it, whether through a user interface or an API",
            "Does the application have a business flow that a person needs a"
            " minimum time to complete?",
            reviewed_by="mstarks01",
        ),
        "limited-resource": _row(
            "the application sells, books or allocates things of limited quantity",
            "Does the application book or allocate things of limited quantity,"
            " such as seats, stock or time slots?",
            reviewed_by="mstarks01",
        ),
        "high-value-flow": _row(
            "the application performs highly sensitive operations, such as money"
            " transfers, access to classified data, contract approvals or"
            " safety overrides",
            "Does the application perform highly sensitive operations, such as"
            " money transfers, classified-data access, contract approvals or"
            " safety overrides?",
            reviewed_by="mstarks01",
        ),
        "multi-system-request": _row(
            "a request or a transaction passes through more than one system",
            "Do requests or transactions pass through more than one system?",
            reviewed_by="mstarks01",
        ),
        # --- Files ---
        "file-upload": _row(
            "the application accepts files from users or other systems, by"
            " upload or import",
            "Does the application accept files from users or other systems?",
            reviewed_by="mstarks01",
        ),
        "archive-files": _row(
            "the application accepts compressed or archive files, including ZIP,"
            " gzip and archive-based document formats",
            "Does the application accept compressed or archive files, including"
            " ZIP, gzip or archive-based document formats?",
            parent="file-upload",
            reviewed_by="mstarks01",
        ),
        "image-files": _row(
            "the application accepts image files",
            "Does the application accept image files?",
            parent="file-upload",
            reviewed_by="mstarks01",
        ),
        "server-side-file-processing": _row(
            "the application's server processes files, for example by extracting"
            " archives, converting or resizing them",
            "Does the application's server process files, such as by extracting,"
            " converting or resizing them?",
            reviewed_by="mstarks01",
        ),
        "public-files-from-untrusted-input": _row(
            "the application stores files that untrusted input produced, uploaded"
            " or generated, where HTTP requests can reach them directly",
            "Does the application store uploaded or generated files from untrusted"
            " input where people can fetch them directly over HTTP?",
            reviewed_by="mstarks01",
        ),
        "untrusted-file-path-input": _row(
            "untrusted filenames or file metadata shape file system paths, in an"
            " upload or any other operation",
            "Can untrusted filenames or file metadata influence a file system"
            " path the application uses?",
            reviewed_by="mstarks01",
        ),
        "file-download": _row(
            "the application sends files to its users, as downloads, exports or"
            " attachments",
            "Does the application send files to users, such as downloads or"
            " email attachments?",
            reviewed_by="mstarks01",
        ),
        "untrusted-file-download": _row(
            "the application serves or sends files obtained from untrusted sources"
            " to users or other systems",
            "Does the application serve or send users or other systems any files"
            " obtained from untrusted sources, such as uploads or imports?",
            reviewed_by="mstarks01",
        ),
        # --- Authentication ---
        "authentication": _row(
            "the application provides or needs authentication of users or"
            " callers, including optional sign-in",
            "Does the application provide, or need, authentication of users or"
            " callers, including optional sign-in?",
            reviewed_by="mstarks01",
        ),
        "multiple-authentication-pathways": _row(
            "the application supports multiple authentication pathways or entry points",
            "Does the application expose more than one authentication pathway, such"
            " as web, mobile, API or recovery endpoints, even when they use the"
            " same authentication method?",
            parent="authentication",
            reviewed_by="mstarks01",
        ),
        "initial-authentication-secrets": _row(
            "the application issues initial passwords or activation secrets",
            "Does the application issue initial passwords or activation secrets,"
            " such as codes or tokens in activation links?",
            reviewed_by="mstarks01",
        ),
        "expiring-authentication-mechanisms": _row(
            "the application uses an authentication mechanism that expires",
            "Does the application use an authentication mechanism that expires,"
            " such as a certificate or a temporary password?",
            reviewed_by="mstarks01",
        ),
        "password-authentication": _row(
            "an in-scope component sets, resets, verifies or stores user"
            " passwords, including a password used as an additional factor",
            "Does an in-scope component set, reset, verify or store user"
            " passwords, including passwords used as an additional factor?",
            parent="authentication",
            reviewed_by="mstarks01",
        ),
        "password-reset": _row(
            "the application supports the reset of a forgotten password",
            "Does the application support resetting a forgotten password, either"
            " through self-service or an assisted process?",
            parent="password-authentication",
            reviewed_by="mstarks01",
        ),
        "administrator-password-reset": _row(
            "an administrator can start the reset of a user's password",
            "Can an administrator initiate or perform a reset of a user's password?",
            parent="password-authentication",
            reviewed_by="mstarks01",
        ),
        "stored-password-verifiers": _row(
            "an in-scope component stores data used to verify user passwords,"
            " regardless of its storage protection",
            "Does an in-scope component store passwords or representations used to"
            " verify them, such as hashes, encrypted passwords or plaintext"
            " passwords?",
            parent="password-authentication",
            reviewed_by="mstarks01",
        ),
        "multi-factor-authentication": _row(
            "the application offers or requires a second authentication factor",
            "Does the application use a second authentication factor?",
            parent="authentication",
            reviewed_by="mstarks01",
        ),
        "one-time-codes": _row(
            "authentication or recovery uses lookup or recovery secrets,"
            " out-of-band codes or approval requests, or time-based one-time"
            " passwords",
            "Does authentication or recovery use lookup/recovery secrets,"
            " out-of-band codes or approval requests, or time-based one-time"
            " passwords?",
            parent="authentication",
            reviewed_by="mstarks01",
        ),
        "lookup-secrets": _row(
            "authentication or recovery uses lookup secrets, such as recovery codes",
            "Does the application accept lookup secrets, such as recovery codes?",
            parent="one-time-codes",
            reviewed_by="mstarks01",
        ),
        "stored-lookup-secrets": _row(
            "the application stores lookup secrets or their verifiers for later"
            " verification",
            "Does the application store lookup or recovery secrets, or hashes or"
            " other verifiers of them?",
            parent="one-time-codes",
            reviewed_by="mstarks01",
        ),
        "lookup-secret-generation": _row(
            "the application generates lookup secrets",
            "Does the application generate lookup secrets, such as recovery codes?",
            parent="one-time-codes",
            reviewed_by="mstarks01",
        ),
        "out-of-band-authentication": _row(
            "authentication uses an out-of-band channel: a code, an approval"
            " request or a token sent through a separate channel",
            "Does authentication use a separate channel, such as a code by SMS"
            " or email, or an approval request on a device?",
            parent="one-time-codes",
            reviewed_by="mstarks01",
        ),
        "out-of-band-codes": _row(
            "authentication uses codes that the application sends out of band"
            " and the user enters",
            "Does the application send codes, such as by SMS or email, that users"
            " enter to authenticate?",
            parent="one-time-codes",
            reviewed_by="mstarks01",
        ),
        "out-of-band-code-generation": _row(
            "the application generates out-of-band authentication codes",
            "Does the application generate the codes it sends for authentication?",
            parent="one-time-codes",
            reviewed_by="mstarks01",
        ),
        "pstn-out-of-band-authentication": _row(
            "out-of-band authentication uses the telephone network, by SMS or a"
            " voice call",
            "Does authentication send codes or approval requests by SMS or phone call?",
            parent="one-time-codes",
            reviewed_by="mstarks01",
        ),
        "push-authentication": _row(
            "authentication sends a push notification for the user to approve",
            "Does authentication use push notifications that users approve?",
            parent="one-time-codes",
            reviewed_by="mstarks01",
        ),
        "totp": _row(
            "authentication uses time-based one-time passwords",
            "Does authentication use time-based one-time passwords, such as from"
            " an authenticator app?",
            parent="one-time-codes",
            reviewed_by="mstarks01",
        ),
        "totp-seed-generation": _row(
            "the application generates the seeds for time-based one-time passwords",
            "Does the application generate the seeds for time-based one-time"
            " passwords?",
            parent="one-time-codes",
            reviewed_by="mstarks01",
        ),
        "biometric-authentication": _row(
            "the application accepts a biometric factor",
            "Does the application accept fingerprints, face or other biometrics?",
            parent="authentication",
            reviewed_by="mstarks01",
        ),
        "cryptographic-authenticators": _row(
            "the application authenticates users with keys or devices: passkeys,"
            " security keys, smart cards or certificates",
            "Do users authenticate with passkeys, security keys, smart cards or"
            " certificates?",
            parent="authentication",
            reviewed_by="mstarks01",
        ),
        "authenticator-certificates": _row(
            "certificates verify cryptographic authentication assertions",
            "Are certificates used to verify cryptographic authentication assertions?",
            parent="cryptographic-authenticators",
            reviewed_by="mstarks01",
        ),
        "federated-identity": _row(
            "users sign in through a separate identity provider (SSO)",
            "Do users sign in through a separate identity provider or single sign-on?",
            parent="authentication",
            reviewed_by="mstarks01",
        ),
        "multiple-identity-providers": _row(
            "one part of the application accepts sign-in from more than one identity"
            " provider",
            "Does any one part of the application accept sign-in from more than one"
            " identity provider, not counting providers that other parts accept?",
            parent="federated-identity",
            reviewed_by="mstarks01",
        ),
        "idp-assurance-policy": _row(
            "a function that users reach through identity provider sign-in needs a"
            " particular authentication strength, method or recency, whether or not"
            " this is documented or enforced",
            "Does any function that users reach through identity provider sign-in"
            " need a particular authentication strength, method or recency, whether"
            " or not that need is documented or enforced?",
            parent="federated-identity",
            reviewed_by="mstarks01",
        ),
        "saml": _row(
            "an in-scope relying party consumes SAML assertions for authentication",
            "Does an in-scope relying party consume SAML assertions for"
            " authentication?",
            parent="federated-identity",
            reviewed_by="mstarks01",
        ),
        "mutual-tls": _row(
            "the application authenticates callers by TLS client certificates",
            "Does the application authenticate callers with TLS client"
            " certificates (mutual TLS)?",
            reviewed_by="mstarks01",
        ),
        # --- Sessions and tokens ---
        "sessions": _row(
            "the application keeps a user signed in across requests, by a"
            " session cookie or a token",
            "Does the application keep users signed in across requests?",
            reviewed_by="mstarks01",
        ),
        "reference-session-tokens": _row(
            "the application's sessions are opaque tokens looked up on the server",
            "Are the application's sessions stored on the server and referenced"
            " by an opaque token?",
            parent="sessions",
            reviewed_by="mstarks01",
        ),
        "self-contained-tokens": _row(
            "the application issues or consumes self-contained tokens, such as"
            " JWTs or SAML assertions, whether or not they are correctly signed",
            "Does the application issue or consume self-contained tokens, such as"
            " JWTs or SAML assertions, whether or not they are correctly signed?",
            reviewed_by="mstarks01",
        ),
        "self-contained-token-consumer": _row(
            "the application consumes self-contained tokens",
            "Does the application consume self-contained tokens, including tokens"
            " it issued itself and later receives back?",
            parent="self-contained-tokens",
            reviewed_by="mstarks01",
        ),
        "self-contained-token-issuer": _row(
            "the application issues self-contained tokens",
            "Does the application issue self-contained tokens, such as JWTs?",
            parent="self-contained-tokens",
            reviewed_by="mstarks01",
        ),
        "token-validity-period": _row(
            "at least one self-contained token consumed by the application contains a"
            " validity time bound, such as an expiry or not-before time",
            "Does any self-contained token the application consumes contain an expiry"
            " time, a not-before time or another validity time bound?",
            parent="self-contained-token-consumer",
            reviewed_by="mstarks01",
        ),
        "shared-signing-key-audiences": _row(
            "the application signs self-contained tokens for more than one audience"
            " with one signing key",
            "Does the application use one signing key for the tokens it issues to more"
            " than one audience?",
            parent="self-contained-token-issuer",
            reviewed_by="mstarks01",
        ),
        # --- OAuth and OpenID Connect ---
        "oauth": _row(
            "the application takes part in OAuth 2.0 or OpenID Connect",
            "Does the application use OAuth 2.0 or OpenID Connect?",
            reviewed_by="mstarks01",
        ),
        "oauth-client": _row(
            "the application obtains tokens from an authorization server as an"
            " OAuth client",
            "Does the application obtain OAuth tokens from an authorization"
            " server, as a client?",
            parent="oauth",
            reviewed_by="mstarks01",
        ),
        "user-agent-authorization-flow": _row(
            "the OAuth client sends the user through a browser or another user"
            " agent to authorize",
            "Does the OAuth client use a browser or another user agent to obtain"
            " user authorization?",
            parent="oauth-client",
            reviewed_by="mstarks01",
        ),
        "multiple-authorization-servers": _row(
            "a single OAuth client in scope can interact with more than one"
            " authorization server in the deployed system",
            "Can any single OAuth client in scope interact with more than one"
            " authorization server, even if it currently uses only one?",
            parent="oauth-client",
            reviewed_by="mstarks01",
        ),
        "client-code-flow": _row(
            "an OAuth client in scope uses an authorization-code exchange to obtain"
            " tokens, including as part of an OpenID Connect hybrid flow",
            "Does any OAuth client in scope obtain tokens by exchanging an"
            " authorization code, including as part of an OpenID Connect hybrid flow?",
            parent="oauth-client",
            reviewed_by="mstarks01",
        ),
        "oidc-relying-party": _row(
            "an OAuth client in the application signs users in with OpenID Connect, as"
            " a relying party",
            "Does an OAuth client in the application sign users in with OpenID"
            " Connect?",
            parent="oauth-client",
            reviewed_by="mstarks01",
        ),
        "oauth-resource-server": _row(
            "the application accepts OAuth access tokens on its API",
            "Does the application's API accept OAuth access tokens?",
            parent="oauth",
            reviewed_by="mstarks01",
        ),
        "user-identity-authorization": _row(
            "the resource server's access decisions need to depend on the user"
            " represented by the token, whether or not that dependence is enforced",
            "Must the API's access decisions depend on which user a token"
            " represents, whether or not that check is currently enforced?",
            parent="oauth-resource-server",
            reviewed_by="mstarks01",
        ),
        "resource-server-assurance-policy": _row(
            "some access to an API that accepts OAuth access tokens needs a"
            " particular authentication strength, method or recency, whether or not"
            " this is documented or enforced",
            "Must any access to the API that accepts OAuth access tokens require a"
            " particular authentication strength, method or recency, whether or not"
            " that requirement is documented or enforced?",
            parent="oauth-resource-server",
            reviewed_by="mstarks01",
        ),
        "oauth-authorization-server": _row(
            "an OAuth authorization server, or its configuration in a managed"
            " service, is in the assessment scope",
            "Is an OAuth authorization server, including its configuration in a"
            " managed service, within the assessment scope?",
            parent="oauth",
            reviewed_by="mstarks01",
        ),
        "server-code-flow": _row(
            "an authorization server in scope supports issuing authorization codes for"
            " exchange into tokens, including as part of an OpenID Connect hybrid flow",
            "Does any authorization server in scope support issuing authorization"
            " codes for exchange into tokens, including as part of an OpenID Connect"
            " hybrid flow?",
            parent="oauth-authorization-server",
            reviewed_by="mstarks01",
        ),
        "oidc-provider": _row(
            "the authorization server in scope is also an OpenID Connect provider",
            "Does the authorization server in scope also act as an OpenID Connect"
            " provider?",
            parent="oauth-authorization-server",
            reviewed_by="mstarks01",
        ),
        "refresh-tokens": _row(
            "the authorization server issues refresh tokens",
            "Does the authorization server issue refresh tokens?",
            parent="oauth-authorization-server",
            reviewed_by="mstarks01",
        ),
        "public-client-refresh-tokens": _row(
            "the authorization server issues refresh tokens to public clients",
            "Does the authorization server issue refresh tokens to public clients,"
            " meaning clients that cannot keep client authentication credentials"
            " confidential, such as a browser-only app or an installed mobile app?",
            parent="oauth-authorization-server",
            reviewed_by="mstarks01",
        ),
        "reference-access-tokens": _row(
            "the authorization server issues opaque reference access tokens",
            "Does the authorization server issue opaque reference access tokens?",
            parent="oauth-authorization-server",
            reviewed_by="mstarks01",
        ),
        "confidential-client-backchannel": _row(
            "confidential clients call the authorization server over a back channel",
            "Do confidential clients call the authorization server directly, such"
            " as at its token endpoint?",
            parent="oauth-authorization-server",
            reviewed_by="mstarks01",
        ),
        "unauthenticated-dynamic-registration": _row(
            "the authorization server accepts dynamic registrations without"
            " authenticating the registering party",
            "Does the authorization server allow dynamic client registration"
            " without authenticating the party requesting registration?",
            parent="oauth-authorization-server",
            reviewed_by="mstarks01",
        ),
        "user-delegated-authorization": _row(
            "the authorization server supports delegated access by clients on"
            " behalf of users, whether or not user consent is correctly obtained",
            "Does the authorization server let clients obtain access on behalf of"
            " users, including pre-authorized access or flows with no consent"
            " screen?",
            parent="oauth-authorization-server",
            reviewed_by="mstarks01",
        ),
        "oidc-backchannel-logout": _row(
            "an OpenID Connect relying party in scope supports receiving back-channel"
            " logout requests directly from an OpenID provider",
            "Does any OpenID Connect relying party in scope support receiving logout"
            " requests directly from an OpenID provider through back-channel logout,"
            " even if none have arrived yet?",
            parent="oidc-relying-party",
            reviewed_by="mstarks01",
        ),
        "rp-initiated-logout": _row(
            "the OpenID provider supports logout that a relying party starts",
            "Does the OpenID provider support logout started by a relying party?",
            parent="oidc-provider",
            reviewed_by="mstarks01",
        ),
        # --- Authorization ---
        "restricted-access": _row(
            "some function or data must be restricted to particular users,"
            " callers, roles or tenants, whether or not the restriction is"
            " enforced yet",
            "Must any function or data be restricted to particular users,"
            " callers, roles or tenants, whether or not that restriction is"
            " currently enforced?",
            reviewed_by="mstarks01",
        ),
        "multi-tenancy": _row(
            "the system serves several tenants whose data or operations need"
            " isolation, including tenants that share backend services",
            "Does the system serve multiple tenants whose data or operations"
            " require isolation, including individual tenants or tenants sharing"
            " backend services?",
            reviewed_by="mstarks01",
        ),
        "administrative-interface": _row(
            "the application has an interface for administrators",
            "Does the application have an administration interface?",
            reviewed_by="mstarks01",
        ),
        # --- Data and cryptography ---
        "sensitive-data": _row(
            "the application handles sensitive data, including personal,"
            " financial or health data, credentials, session tokens, keys or"
            " confidential business information",
            "Does the application handle sensitive data, including personal,"
            " financial or health data, credentials, session tokens, keys or"
            " confidential business information?",
            reviewed_by="mstarks01",
        ),
        "encryption": _row(
            "an in-scope component encrypts stored or application data beyond"
            " the transport, including configured managed storage",
            "Does an in-scope component encrypt stored or application data beyond"
            " transport encryption, including configured managed storage?",
            reviewed_by="mstarks01",
        ),
        "separate-cipher-and-mac": _row(
            "the application's encryption combines a cipher with a separate MAC",
            "Does the application's encryption use a separate cipher and MAC?",
            parent="encryption",
            reviewed_by="mstarks01",
        ),
        # --- Connections between components ---
        "internal-services": _row(
            "the application's backend is split into components that talk over"
            " a network, such as services, middleware or a database server",
            "Is the application's backend made of separate components that talk"
            " over a network?",
            reviewed_by="mstarks01",
        ),
        "internal-http-connections": _row(
            "the application's own components communicate using HTTP, including"
            " HTTP over TLS (HTTPS)",
            "Do the application's own components communicate over HTTP or HTTPS?",
            reviewed_by="mstarks01",
        ),
        "internal-tls-connections": _row(
            "the application's own components talk to each other over TLS",
            "Do the application's own components talk to each other over TLS?",
            reviewed_by="mstarks01",
        ),
        "backend-service-connections": _row(
            "backend components communicate with services",
            "Do backend components communicate with other services?",
            reviewed_by="mstarks01",
        ),
        "service-accounts": _row(
            "the application uses accounts with services, including local"
            " operating system accounts",
            "Does the application use accounts to access services or to run under a"
            " local operating system identity, including shared, default or"
            " privileged accounts?",
            reviewed_by="mstarks01",
        ),
        "backend-service-credentials": _row(
            "a backend component presents credentials to a service",
            "Do backend components present credentials, such as passwords, keys"
            " or tokens, to other services?",
            reviewed_by="mstarks01",
        ),
        "separate-service-connections": _row(
            "the application connects to a separate internal or external service",
            "Does the application connect to separate internal or external services?",
            reviewed_by="mstarks01",
        ),
        # --- WebRTC ---
        "webrtc": _row(
            "the application uses WebRTC for real-time media or data",
            "Does the application use WebRTC for calls, video or real-time data?",
            reviewed_by="mstarks01",
        ),
        "turn-server": _row(
            "a TURN relay server, or its managed-service configuration, is in"
            " scope for the application",
            "Is a TURN relay server, or its managed-service configuration, in"
            " scope for this application?",
            parent="webrtc",
            reviewed_by="mstarks01",
        ),
        "media-server": _row(
            "a WebRTC media server, or its managed-service configuration, is in"
            " scope for the application",
            "Is a WebRTC media server, or its managed-service configuration, in"
            " scope for this application?",
            parent="webrtc",
            reviewed_by="mstarks01",
        ),
        "media-recording": _row(
            "an in-scope WebRTC media server, or a recording service beside it,"
            " records audio or video",
            "Does an in-scope WebRTC media server, or an associated recording"
            " service, record audio or video?",
            parent="media-server",
            reviewed_by="mstarks01",
        ),
        "signaling-server": _row(
            "a WebRTC signaling server, or its managed-service configuration, is"
            " in scope for the application",
            "Is a WebRTC signaling server, or its managed-service configuration,"
            " in scope for this application?",
            parent="webrtc",
            reviewed_by="mstarks01",
        ),
        # --- Encoding, parsing and interpreters ---
        "dynamic-url-construction": _row(
            "the application builds URLs from untrusted data",
            "Does the application build URLs from untrusted data?",
            reviewed_by="mstarks01",
        ),
        "dynamic-javascript-or-json-output": _row(
            "the application constructs JavaScript or JSON at run time, including"
            " through serializers",
            "Does the application construct JavaScript or JSON at run time,"
            " including through a serializer?",
            reviewed_by="mstarks01",
        ),
        "untrusted-data-in-dangerous-context": _row(
            "the application passes data originating from users or other untrusted"
            " sources into contexts that interpret it, including when encoding or"
            " sanitization is already applied",
            "Does the application use data from users or other untrusted sources in"
            " an interpreted context, such as a query, command or template,"
            " including when that data is already encoded or sanitized?",
            reviewed_by="mstarks01",
        ),
        "untrusted-format-strings": _row(
            "the application processes format strings, including fixed strings and"
            " strings influenced by external input",
            "Does the application process format strings, such as patterns with"
            " placeholders for inserting values into text?",
            reviewed_by="mstarks01",
        ),
        "regex-processing": _row(
            "the application evaluates regular expressions, including fixed"
            " patterns against untrusted strings",
            "Does the application evaluate regular expressions, including fixed"
            " patterns against untrusted input?",
            reviewed_by="mstarks01",
        ),
        "untrusted-data-deserialization": _row(
            "the application reconstructs data or objects from untrusted serialized"
            " input",
            "Does the application read serialized input, such as JSON, XML or"
            " stored object data, from users or other untrusted sources and turn it"
            " into data structures or objects?",
            reviewed_by="mstarks01",
        ),
        "multiple-parsers-same-data": _row(
            "different parsers in the application interpret the same type of data",
            "Do different parsers in the application interpret the same type of"
            " data, such as JSON or URLs?",
            reviewed_by="mstarks01",
        ),
        # --- Validation and business logic ---
        "related-data-consistency": _row(
            "the application handles related data items whose valid values depend"
            " on each other or on their context",
            "Does the application handle related data items whose valid values"
            " depend on each other or on context, such as a postal code and"
            " locality or a start and end date?",
            reviewed_by="mstarks01",
        ),
        "business-logic-limits-needed": _row(
            "the application has business operations with valid bounds on amounts,"
            " quantities, frequency or other business values, per user or across"
            " the application, whether those bounds are enforced or not",
            "Does the application have business operations where amounts,"
            " quantities, frequency or other values can exceed what the business"
            " allows, for one user or across the application?",
            reviewed_by="mstarks01",
        ),
        "atomic-business-operation-needed": _row(
            "a state-changing business operation must complete wholly or restore"
            " its earlier valid state",
            "Does the application have a business operation that changes state and"
            " must complete wholly or not at all?",
            reviewed_by="mstarks01",
        ),
        # --- HTTP ---
        "http-response-bodies": _row(
            "the application produces HTTP responses that contain message bodies",
            "Does the application produce HTTP responses that contain a body?",
            reviewed_by="mstarks01",
        ),
        "intermediary-header-consumption": _row(
            "the application consumes HTTP headers that an intermediary sets",
            "Does the application read HTTP headers that a proxy, load balancer or"
            " gateway sets?",
            reviewed_by="mstarks01",
        ),
        "http-content-length-generation": _row(
            "an in-scope component, including a framework or a proxy, sets"
            " Content-Length in the HTTP messages it generates",
            "Does an in-scope component, including a framework or proxy, set"
            " Content-Length on the HTTP messages it generates?",
            reviewed_by="mstarks01",
        ),
        "http2-messages": _row(
            "an in-scope component sends or accepts HTTP/2 messages",
            "Does an in-scope component send or accept HTTP/2 messages?",
            reviewed_by="mstarks01",
        ),
        "http3-messages": _row(
            "an in-scope component sends or accepts HTTP/3 messages",
            "Does an in-scope component send or accept HTTP/3 messages?",
            reviewed_by="mstarks01",
        ),
        "http2-request-reception": _row(
            "an in-scope component accepts HTTP/2 requests",
            "Does an in-scope component accept HTTP/2 requests?",
            reviewed_by="mstarks01",
        ),
        "http3-request-reception": _row(
            "an in-scope component accepts HTTP/3 requests",
            "Does an in-scope component accept HTTP/3 requests?",
            reviewed_by="mstarks01",
        ),
        "external-facing-http-services": _row(
            "the application exposes HTTP services to clients outside its boundary,"
            " including clients on a private network",
            "Does the application expose HTTP services to clients outside its own"
            " boundary, including clients on a private network?",
            reviewed_by="mstarks01",
        ),
        "intermediary-client-ip-handling": _row(
            "client requests reach the application or its web server through a"
            " proxy or middleware, whether original client IP information is"
            " forwarded or not",
            "Do client requests reach the application or its web server through a"
            " proxy or middleware, whether or not it forwards the original client"
            " IP address?",
            reviewed_by="mstarks01",
        ),
        "data-object-responses": _row(
            "the application returns the fields of data objects to its consumers",
            "Does the application return data objects, or their fields, to its"
            " consumers?",
            reviewed_by="mstarks01",
        ),
        "request-object-field-writes": _row(
            "request data writes object fields, by automatic or manual binding",
            "Does the application use data from requests to set or update object"
            " fields, either automatically through a framework or explicitly in"
            " code?",
            reviewed_by="mstarks01",
        ),
        "web-tier-file-serving": _row(
            "the application has a web server or other web-facing component capable"
            " of serving files, including files not intended for publication",
            "Can the application's web server or another web-facing component serve"
            " files, including files not intended to be public?",
            reviewed_by="mstarks01",
        ),
        # --- Cryptography ---
        "cryptographic-keys": _row(
            "an in-scope component uses cryptographic keys",
            "Does an in-scope component use cryptographic keys?",
            reviewed_by="mstarks01",
        ),
        "cryptographic-operations": _row(
            "an in-scope component uses cryptography, including through managed"
            " services and TLS",
            "Does an in-scope component use cryptography, including through a"
            " managed service or TLS?",
            reviewed_by="mstarks01",
        ),
        "cryptographic-hashing": _row(
            "the application hashes data for a cryptographic purpose, including"
            " with an insecure implementation",
            "Does the application hash data for a security purpose?",
            reviewed_by="mstarks01",
        ),
        "authenticity-or-integrity-hashing": _row(
            "hashes support signatures, data authentication or integrity in the"
            " application",
            "Does the application use hashes for signatures, data authentication or"
            " integrity?",
            reviewed_by="mstarks01",
        ),
        "password-derived-secret-keys": _row(
            "the application derives secret keys from passwords",
            "Does the application derive secret keys from passwords?",
            reviewed_by="mstarks01",
        ),
        "unpredictable-values-needed": _row(
            "the application generates values that must be unpredictable, even if"
            " they are predictable now",
            "Does the application generate values that must not be guessable, such"
            " as tokens or identifiers?",
            reviewed_by="mstarks01",
        ),
        "random-value-generation": _row(
            "the application generates random values, including with an insecure"
            " generator",
            "Does the application generate random values?",
            reviewed_by="mstarks01",
        ),
        "non-tls-public-key-operations": _row(
            "the application generates public keys or creates or verifies"
            " signatures outside TLS",
            "Does the application generate public keys, or create or verify"
            " signatures, outside TLS?",
            reviewed_by="mstarks01",
        ),
        "non-tls-key-exchange": _row(
            "the application uses public-key exchange outside TLS",
            "Does the application use public-key exchange outside TLS?",
            reviewed_by="mstarks01",
        ),
        # --- Secure communication ---
        "tls-use": _row(
            "an in-scope component uses or supports TLS, including managed endpoints",
            "Does an in-scope component use or support TLS, including a managed"
            " endpoint?",
            reviewed_by="mstarks01",
        ),
        "tls-certificates": _row(
            "the in-scope TLS uses certificates",
            "Does any in-scope component use certificates for TLS, including"
            " through a managed endpoint?",
            reviewed_by="mstarks01",
        ),
        "tls-client": _row(
            "an in-scope component acts as a TLS client",
            "Does an in-scope component connect to other services as a TLS client?",
            reviewed_by="mstarks01",
        ),
        # --- Configuration and resources ---
        "external-resource-use": _row(
            "the application consumes external resources, including services,"
            " files, threads or connections",
            "Does the application use resources such as databases, other services,"
            " files, threads or network connections?",
            reviewed_by="mstarks01",
        ),
        "backend-secrets": _row(
            "backend secrets exist in scope, including secrets in managed custody"
            " or in insecure storage",
            "Does the application's backend use or manage secrets, such as"
            " passwords, keys or tokens, including secrets held in memory, embedded"
            " in code or managed by another in-scope service?",
            reviewed_by="mstarks01",
        ),
        "secret-assets": _row(
            "secrets exist within the application's scope, including secrets used"
            " or managed on its behalf by an in-scope service",
            "Does the application use, store or manage secrets, including through"
            " another in-scope service?",
            reviewed_by="mstarks01",
        ),
        # --- Secure coding and architecture ---
        "third-party-components": _row(
            "the application has in-scope third-party dependencies, including"
            " runtime and transitive ones",
            "Does the application use third-party components, including runtime and"
            " transitive dependencies?",
            reviewed_by="mstarks01",
        ),
        "resource-demanding-functionality": _row(
            "the application has functionality that can consume enough time or"
            " computing resources to impair availability, whether protections are"
            " already present or not",
            "Does the application have functionality whose time or resource"
            " consumption could impair availability if it were used heavily or"
            " without limits?",
            reviewed_by="mstarks01",
        ),
        "dangerous-functionality": _row(
            "the application has dangerous functionality, whether documented or not",
            "Does the application have dangerous functionality, such as functions"
            " that can damage data or the system?",
            reviewed_by="mstarks01",
        ),
        "risky-third-party-components": _row(
            "the application uses risky third-party components, whether documented"
            " or not",
            "Does the application use third-party components that carry particular"
            " risk?",
            reviewed_by="mstarks01",
        ),
        "shared-multithreaded-state": _row(
            "multiple threads in the application access shared objects, including"
            " through framework behaviour",
            "Do multiple threads in the application access shared objects?",
            reviewed_by="mstarks01",
        ),
        "check-dependent-resource-actions": _row(
            "the application's actions depend on checks of a resource's state, such"
            " as its existence or permissions",
            "Does an application action depend on a resource's state, such as"
            " whether a file exists or a user has permission, including when the"
            " check and action happen together?",
            reviewed_by="mstarks01",
        ),
        "concurrency-locks": _row(
            "the application's concurrent code uses locks",
            "Does the application's concurrent code use locks?",
            reviewed_by="mstarks01",
        ),
        "contended-thread-resources": _row(
            "threads in the application compete for the allocation of resources",
            "Do threads in the application compete for the allocation of resources?",
            reviewed_by="mstarks01",
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
