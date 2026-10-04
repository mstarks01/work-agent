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
        ),
        "browser-accessible-http": _row(
            "the application serves HTTP content that a web browser can open,"
            " including API and file responses with no frontend",
            "Does the application serve pages, scripts, files or HTTP responses"
            " that people can open in a web browser?",
        ),
        "postmessage-receiver": _row(
            "the application's browser code receives messages from other documents"
            " or windows, such as through postMessage",
            "Does the application's browser code receive messages from other"
            " windows or frames, such as through postMessage?",
        ),
        "external-browser-resources": _row(
            "the application's pages load scripts, styles or other resources"
            " hosted outside the application",
            "Do the application's pages load scripts, styles or other resources"
            " from another host, such as a CDN?",
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
        ),
        "sensitive-http-requests": _row(
            "the application's HTTP requests carry sensitive data, including"
            " credentials and session tokens",
            "Do HTTP requests to or from the application carry sensitive data,"
            " such as credentials or session tokens?",
        ),
        # --- Code and runtimes ---
        "javascript-code": _row(
            "an in-scope component executes JavaScript, in a browser, a server,"
            " a native app or an embedded runtime",
            "Does any in-scope component execute JavaScript, including browser,"
            " server, native or embedded code?",
        ),
        "memory-unsafe-code": _row(
            "part of the application is written in a language without memory"
            " safety, such as C or C++",
            "Is any part of the application written in C, C++ or another"
            " language without memory safety?",
            reviewed_by="mstarks01",
        ),
        "overflow-capable-arithmetic": _row(
            "the application does arithmetic whose overflow can change its"
            " behaviour, including fixed-width arithmetic in a memory-safe language",
            "Does the application do fixed-width arithmetic whose overflow could"
            " change what it does?",
        ),
        "manually-managed-resources": _row(
            "the application allocates memory or other resources that its code"
            " must release explicitly",
            "Does the application's code allocate memory or other resources that"
            " it must release explicitly?",
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
        ),
        "dynamic-regex": _row(
            "the application builds regular expressions from untrusted input,"
            " including data it receives from other systems",
            "Does the application construct regular expressions using untrusted"
            " input, including data received indirectly from other systems?",
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
        ),
        "untrusted-html": _row(
            "the application accepts HTML from an untrusted source",
            "Does the application accept HTML from users or other untrusted sources?",
            parent="rich-text-input",
        ),
        "untrusted-svg": _row(
            "the application accepts SVG from an untrusted source",
            "Does the application accept SVG from users or other untrusted sources?",
            parent="rich-text-input",
        ),
        "untrusted-scriptable-content": _row(
            "the application accepts untrusted content that can carry scripts or"
            " expressions, such as Markdown, CSS, XSL or BBCode",
            "Does the application accept Markdown, CSS, XSL, BBCode or similar"
            " content from users or other untrusted sources?",
            parent="rich-text-input",
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
        ),
        "untrusted-outbound-destination": _row(
            "untrusted input, direct or indirect, shapes any part of a"
            " server-side call to another service, by HTTP or another scheme",
            "Can untrusted input influence any part of a server-side call to"
            " another service, such as its scheme, host, port or path?",
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
        ),
        "multi-system-request": _row(
            "a request or a transaction passes through more than one system",
            "Do requests or transactions pass through more than one system?",
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
        ),
        "untrusted-file-source": _row(
            "the application receives files from an untrusted source, by upload,"
            " import or another route",
            "Does the application receive files from users, imports or other"
            " untrusted sources?",
        ),
        "public-files-from-untrusted-input": _row(
            "the application stores files that untrusted input produced, uploaded"
            " or generated, where HTTP requests can reach them directly",
            "Does the application store uploaded or generated files from untrusted"
            " input where people can fetch them directly over HTTP?",
        ),
        "untrusted-file-path-input": _row(
            "untrusted filenames or file metadata shape file system paths, in an"
            " upload or any other operation",
            "Can untrusted filenames or file metadata influence a file system"
            " path the application uses?",
        ),
        "file-download": _row(
            "the application sends files to its users, as downloads, exports or"
            " attachments",
            "Does the application send files to users, such as downloads or"
            " email attachments?",
            reviewed_by="mstarks01",
        ),
        # --- Authentication ---
        "authentication": _row(
            "the application provides or needs authentication of users or"
            " callers, including optional sign-in",
            "Does the application provide, or need, authentication of users or"
            " callers, including optional sign-in?",
        ),
        "multiple-authentication-pathways": _row(
            "the application supports more than one way to authenticate",
            "Does the application support more than one way to sign in or"
            " authenticate?",
            parent="authentication",
        ),
        "initial-authentication-secrets": _row(
            "the application issues initial passwords or activation secrets",
            "Does the application issue initial passwords or activation codes?",
        ),
        "expiring-authentication-mechanisms": _row(
            "the application uses an authentication mechanism that expires",
            "Does the application use an authentication mechanism that expires,"
            " such as a certificate or a temporary password?",
        ),
        "password-authentication": _row(
            "an in-scope component sets, resets, verifies or stores user"
            " passwords, including a password used as an additional factor",
            "Does an in-scope component set, reset, verify or store user"
            " passwords, including passwords used as an additional factor?",
            parent="authentication",
        ),
        "password-reset": _row(
            "the application supports the reset of a forgotten password",
            "Can users reset a forgotten password?",
            parent="password-authentication",
        ),
        "administrator-password-reset": _row(
            "an administrator can start the reset of a user's password",
            "Can an administrator reset a user's password?",
            parent="password-authentication",
        ),
        "stored-password-verifiers": _row(
            "an in-scope component stores password verifiers, such as password hashes",
            "Does an in-scope component store password hashes or other password"
            " verifiers?",
            parent="password-authentication",
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
        ),
        "lookup-secrets": _row(
            "authentication or recovery uses lookup secrets, such as recovery codes",
            "Does the application accept lookup secrets, such as recovery codes?",
            parent="one-time-codes",
        ),
        "stored-lookup-secrets": _row(
            "the application stores lookup secrets to verify them later",
            "Does the application store lookup secrets, such as recovery codes?",
            parent="one-time-codes",
        ),
        "lookup-secret-generation": _row(
            "the application generates lookup secrets",
            "Does the application generate lookup secrets, such as recovery codes?",
            parent="one-time-codes",
        ),
        "out-of-band-authentication": _row(
            "authentication uses an out-of-band channel: a code, an approval"
            " request or a token sent through a separate channel",
            "Does authentication use a separate channel, such as a code by SMS"
            " or email, or an approval request on a device?",
            parent="one-time-codes",
        ),
        "out-of-band-codes": _row(
            "authentication uses codes that the application sends out of band"
            " and the user enters",
            "Does the application send codes, such as by SMS or email, that users"
            " enter to authenticate?",
            parent="one-time-codes",
        ),
        "out-of-band-code-generation": _row(
            "the application generates out-of-band authentication codes",
            "Does the application generate the codes it sends for authentication?",
            parent="one-time-codes",
        ),
        "pstn-out-of-band-authentication": _row(
            "out-of-band authentication uses the telephone network, by SMS or a"
            " voice call",
            "Does authentication send codes or approval requests by SMS or phone call?",
            parent="one-time-codes",
        ),
        "push-authentication": _row(
            "authentication sends a push notification for the user to approve",
            "Does authentication use push notifications that users approve?",
            parent="one-time-codes",
        ),
        "totp": _row(
            "authentication uses time-based one-time passwords",
            "Does authentication use time-based one-time passwords, such as from"
            " an authenticator app?",
            parent="one-time-codes",
        ),
        "totp-seed-generation": _row(
            "the application generates the seeds for time-based one-time passwords",
            "Does the application generate the seeds for time-based one-time"
            " passwords?",
            parent="one-time-codes",
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
        ),
        "federated-identity": _row(
            "users sign in through a separate identity provider (SSO)",
            "Do users sign in through a separate identity provider or single sign-on?",
            parent="authentication",
            reviewed_by="mstarks01",
        ),
        "multiple-identity-providers": _row(
            "the application accepts more than one identity provider",
            "Does the application accept sign-in from more than one identity provider?",
            parent="federated-identity",
        ),
        "authentication-assurance-policy": _row(
            "access requires a stated authentication strength, method or recency,"
            " whether or not the application enforces it yet",
            "Does any access require a particular authentication strength, method"
            " or recency?",
        ),
        "saml": _row(
            "an in-scope relying party consumes SAML assertions for authentication",
            "Does an in-scope relying party consume SAML assertions for"
            " authentication?",
            parent="federated-identity",
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
        ),
        "self-contained-token-consumer": _row(
            "the application consumes self-contained tokens",
            "Does the application accept self-contained tokens, such as JWTs,"
            " from another party?",
            parent="self-contained-tokens",
        ),
        "self-contained-token-issuer": _row(
            "the application issues self-contained tokens",
            "Does the application issue self-contained tokens, such as JWTs?",
            parent="self-contained-tokens",
        ),
        "token-validity-period": _row(
            "a self-contained token states the period in which it is valid",
            "Do the self-contained tokens carry a validity period, such as an"
            " expiry time?",
            parent="self-contained-tokens",
        ),
        "shared-signing-key-audiences": _row(
            "one signing key signs self-contained tokens for more than one audience",
            "Does one signing key sign tokens for more than one audience?",
            parent="self-contained-tokens",
        ),
        # --- OAuth and OpenID Connect ---
        "oauth": _row(
            "the application takes part in OAuth 2.0 or OpenID Connect",
            "Does the application use OAuth 2.0 or OpenID Connect?",
            reviewed_by="mstarks01",
        ),
        "authorization-code-flow": _row(
            "an OAuth party in scope uses the authorization code grant",
            "Does an OAuth client or authorization server here use the"
            " authorization code flow?",
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
            "Does the OAuth client send users through a browser to authorize it?",
            parent="oauth-client",
        ),
        "multiple-authorization-servers": _row(
            "the OAuth client works with more than one authorization server",
            "Does the OAuth client work with more than one authorization server?",
            parent="oauth-client",
        ),
        "oauth-resource-server": _row(
            "the application accepts OAuth access tokens on its API",
            "Does the application's API accept OAuth access tokens?",
            parent="oauth",
            reviewed_by="mstarks01",
        ),
        "user-identity-authorization": _row(
            "the resource server's access decisions depend on the user's identity",
            "Do the API's access decisions depend on which user a token represents?",
            parent="oauth-resource-server",
        ),
        "oauth-authorization-server": _row(
            "an OAuth authorization server, or its configuration in a managed"
            " service, is in the assessment scope",
            "Is an OAuth authorization server, including its configuration in a"
            " managed service, within the assessment scope?",
            parent="oauth",
        ),
        "refresh-tokens": _row(
            "the authorization server issues refresh tokens",
            "Does the authorization server issue refresh tokens?",
            parent="oauth-authorization-server",
        ),
        "public-client-refresh-tokens": _row(
            "the authorization server issues refresh tokens to public clients",
            "Does the authorization server issue refresh tokens to public"
            " clients, such as browser or mobile apps?",
            parent="oauth-authorization-server",
        ),
        "reference-access-tokens": _row(
            "the authorization server issues opaque reference access tokens",
            "Does the authorization server issue opaque reference access tokens?",
            parent="oauth-authorization-server",
        ),
        "confidential-client-backchannel": _row(
            "confidential clients call the authorization server over a back channel",
            "Do confidential clients call the authorization server directly, such"
            " as at its token endpoint?",
            parent="oauth-authorization-server",
        ),
        "unauthenticated-dynamic-registration": _row(
            "the authorization server lets clients register dynamically without"
            " authentication",
            "Does the authorization server let clients register dynamically"
            " without authenticating?",
            parent="oauth-authorization-server",
        ),
        "user-delegated-authorization": _row(
            "users delegate access to clients through the authorization server",
            "Do users grant clients access through the authorization server, such"
            " as on a consent screen?",
            parent="oauth-authorization-server",
        ),
        "oidc": _row(
            "the application uses OpenID Connect for sign-in",
            "Does the application use OpenID Connect for sign-in?",
            parent="oauth",
            reviewed_by="mstarks01",
        ),
        "oidc-backchannel-logout": _row(
            "the relying party receives OpenID Connect back-channel logout requests",
            "Does the relying party receive back-channel logout requests from the"
            " OpenID provider?",
            parent="oidc",
        ),
        "rp-initiated-logout": _row(
            "the OpenID provider supports logout that a relying party starts",
            "Does the OpenID provider support logout started by a relying party?",
            parent="oidc",
        ),
        # --- Authorization ---
        "restricted-access": _row(
            "some function or data must be restricted to particular users,"
            " callers, roles or tenants, whether or not the restriction is"
            " enforced yet",
            "Must any function or data be restricted to particular users,"
            " callers, roles or tenants, whether or not that restriction is"
            " currently enforced?",
        ),
        "multi-tenancy": _row(
            "the system serves several tenants whose data or operations need"
            " isolation, including tenants that share backend services",
            "Does the system serve multiple tenants whose data or operations"
            " require isolation, including individual tenants or tenants sharing"
            " backend services?",
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
        ),
        "encryption": _row(
            "an in-scope component encrypts stored or application data beyond"
            " the transport, including configured managed storage",
            "Does an in-scope component encrypt stored or application data beyond"
            " transport encryption, including configured managed storage?",
        ),
        "separate-cipher-and-mac": _row(
            "the application's encryption combines a cipher with a separate MAC",
            "Does the application's encryption use a separate cipher and MAC?",
            parent="encryption",
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
            "the application's own components talk to each other over HTTP",
            "Do the application's own components talk to each other over HTTP?",
        ),
        "internal-tls-connections": _row(
            "the application's own components talk to each other over TLS",
            "Do the application's own components talk to each other over TLS?",
        ),
        "backend-service-connections": _row(
            "backend components communicate with services",
            "Do backend components communicate with other services?",
        ),
        "service-accounts": _row(
            "the application uses accounts with services, including local"
            " operating system accounts",
            "Does the application use service accounts, including local"
            " operating system accounts?",
        ),
        "backend-service-credentials": _row(
            "a backend component presents credentials to a service",
            "Do backend components present credentials, such as passwords, keys"
            " or tokens, to other services?",
        ),
        "separate-service-connections": _row(
            "the application connects to a separate internal or external service",
            "Does the application connect to separate internal or external services?",
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
        ),
        "media-server": _row(
            "a WebRTC media server, or its managed-service configuration, is in"
            " scope for the application",
            "Is a WebRTC media server, or its managed-service configuration, in"
            " scope for this application?",
            parent="webrtc",
        ),
        "media-recording": _row(
            "an in-scope WebRTC media server, or a recording service beside it,"
            " records audio or video",
            "Does an in-scope WebRTC media server, or an associated recording"
            " service, record audio or video?",
            parent="media-server",
        ),
        "signaling-server": _row(
            "a WebRTC signaling server, or its managed-service configuration, is"
            " in scope for the application",
            "Is a WebRTC signaling server, or its managed-service configuration,"
            " in scope for this application?",
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
