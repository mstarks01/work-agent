"""One page, served safely: what authorises it, and what escapes into it.

The three local apps — the first-run app, the review app and the sitting app —
each serve HTML built from a template string, on loopback, to one operator. All
three put the operator's own text into that HTML, and the first-run app also
puts a submitter's untrusted prose there. Getting a value into a page safely is
therefore one problem with one answer, and this module is that answer.

A page declares what it does, and the policy follows. :class:`Grants` names the
three things a local page may be authorised to do, and :meth:`Grants.policy`
turns that declaration into the header. Every field defaults to ``False``, so a
page that says nothing is granted nothing, and falls through to
``default-src 'none'`` (OWASP A02, deny by default). The three closed directives
are spelled on every policy rather than left to fall back, because none of them
falls back to ``default-src``. Without them a policy can read as total and still
leave a page framable.

Where a value lands decides its escape, and there are exactly two places.
:func:`escape` is for markup, where the value sits in element text or an
attribute. :func:`script_json` is for a ``<script>`` block, which decodes no
HTML entities, so ``html.escape`` would deliver ``&quot;`` where a quote was. It
also closes every ``<`` as ``\u003c``, because a value that spells
``</script>`` ends the block, and everything after it parses as HTML. That last
one is the stored-input XSS this module exists to make unspellable (OWASP A05,
LLM05). The first-run app's report payload carries a submitter's own
description, and it takes the same escape as any other value in a script block,
through the same function.

The nonce is stamped before the fields are filled, and never after. A field's
value is content, and content that happens to spell the placeholder must come
back as those characters rather than become a live nonce.

:func:`is_same_origin` and ``frame-ancestors 'none'`` sit here together on
purpose. They are one control in two halves, and neither replaces the other. A
page framed by somebody else sends requests the header check passes, because
they really do come from this app's own page, and only the directive stops the
frame. Splitting them across modules is how one of them gets edited alone.
"""

from __future__ import annotations

import html
import json
import secrets
from dataclasses import dataclass

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Every template carries this in each inline ``<style>`` and ``<script>`` tag.
#: A block added later without one simply stops running rather than running
#: unauthorised.
NONCE_PLACEHOLDER = "__CSP_NONCE__"

#: The two host spellings a person types, and nothing else. A local app binds
#: loopback, so a request arriving under any other name is a DNS rebind: the
#: browser resolves an attacker's name to 127.0.0.1, no CORS preflight applies,
#: and a write endpoint becomes reachable from any page the operator visits.
#: The ledger these apps write is the record every published quality number
#: rests on (OWASP A01).
LOOPBACK_HOSTS = ["127.0.0.1", "localhost"]

#: Closed on every page, and spelled rather than assumed. None of the three
#: falls back to ``default-src``, so a policy that omits them is open on all
#: three however total the rest of it reads.
_CLOSED = ("base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'")


class MissingPlaceholder(RuntimeError):
    """A template and its caller disagree about what the page holds."""


@dataclass(frozen=True)
class Grants:
    """What one page does, and therefore what its policy authorises.

    Declared per page, beside the template it belongs to, rather than written
    out as a policy string each app keeps its own copy of. Three pages shipped
    three copies of one string and a fourth would have shipped a fourth.

    Every field defaults to ``False``. A new page is granted nothing until it
    says what it does, which is the direction a mistake should fail in.
    """

    #: The page runs an inline ``<script>`` block.
    script: bool = False
    #: The page carries an inline ``<style>`` block.
    style: bool = False
    #: The page fetches its own origin. Never anything wider: a local app that
    #: reaches a second origin is doing something this vocabulary cannot say.
    connect: bool = False

    def policy(self, nonce: str) -> str:
        """This declaration as a Content-Security-Policy header value."""
        directives = ["default-src 'none'"]
        if self.script:
            directives.append(f"script-src 'nonce-{nonce}'")
        if self.style:
            directives.append(f"style-src 'nonce-{nonce}'")
        if self.connect:
            directives.append("connect-src 'self'")
        directives.extend(_CLOSED)
        return "; ".join(directives)


@dataclass(frozen=True)
class RenderedPage:
    """A page and the policy that authorises it, built together.

    One value rather than two, so "serve the HTML, forget the header" is
    unspellable rather than merely discouraged.
    """

    html: str
    csp: str


def escape(text: str) -> str:
    """Escape a value for the page's **markup**.

    :func:`html.escape` rather than a hand-rolled replace of ``&<>``: that one
    is right in element text and wrong in an attribute value, so where a value
    goes stops being part of whether the escape is adequate.

    Not for a ``<script>`` block. Nothing decodes HTML entities there, so this
    is the wrong escape and :func:`script_json` is the right one.
    """
    return html.escape(text)


def script_json(value: object) -> str:
    """A value as a JavaScript literal, for a placeholder inside ``<script>``.

    Two things have to be true and neither is HTML escaping. The value has to
    survive as a JavaScript literal, which is what :func:`json.dumps` gives.
    And it must not close the block: a value spelling ``</script>`` ends it and
    the rest of the page parses as HTML, so every ``<`` goes out as
    ``\\u003c``.

    One function for both callers that need it — a small field a template
    interpolates, and the first-run app's whole report payload, which carries a
    submitter's own prose. The payload is the one with an attacker behind it,
    and it is the reason this is not a formality.
    """
    return json.dumps(value).replace("<", "\\u003c")


def render(template: str, grants: Grants, **fields: str) -> RenderedPage:
    """Fill a page template, and stamp the nonce its policy authorises.

    Substitution is by explicit ``<!--name-->`` replacement rather than
    ``str.format``: the templates contain CSS and JavaScript, which are full of
    braces ``format`` would read as fields.

    Each value in ``fields`` must already carry the escape its position asks
    for — :func:`escape` in markup, :func:`script_json` inside a script block.
    This cannot choose for the caller, because only the template says where a
    placeholder sits.

    A field whose placeholder the template does not carry **raises**. The two
    disagree about what the page holds, and a page served with a value silently
    dropped is the worse of the two outcomes: the first-run app's report page is
    the whole payload, and an empty one renders as a report of nothing rather
    than as a failure.
    """
    nonce = secrets.token_urlsafe(16)
    page = template.replace(NONCE_PLACEHOLDER, nonce)
    for name, value in fields.items():
        placeholder = f"<!--{name}-->"
        if placeholder not in page:
            raise MissingPlaceholder(f"the template carries no {placeholder}")
        page = page.replace(placeholder, value)
    return RenderedPage(html=page, csp=grants.policy(nonce))


def response(page: RenderedPage, status_code: int = 200) -> HTMLResponse:
    """The only way a local app serves HTML, so no page is served bare."""
    return HTMLResponse(
        page.html,
        status_code=status_code,
        headers={"Content-Security-Policy": page.csp},
    )


def is_same_origin(request: Request) -> bool:
    """Whether the browser marked this request as sent from the app's own page.

    The header is browser-set and script cannot spoof it, so this is the check
    every writing endpoint in every local app runs before it does anything. One
    function rather than one line repeated at each endpoint: the header name and
    the accepted value are the whole of the check, and spelled four times they
    are four chances to accept ``same-site`` by typo.

    **Necessary and not sufficient.** See this module's own docstring for the
    other half.

    Callers answer a refusal in their own shape — the first-run app owes its
    form page a ``message`` and the other two raise — so this decides and does
    not respond.
    """
    return request.headers.get("sec-fetch-site") == "same-origin"


def refuse_cross_origin(request: Request) -> None:
    """:func:`is_same_origin`, as the 403 the two eval-side apps answer with.

    They both want the same refusal from every writing endpoint, so the status
    and the sentence live here once rather than at each of the four.
    """
    if not is_same_origin(request):
        raise HTTPException(
            status_code=403, detail="this request did not come from the app's page"
        )


class SecurityHeaders:
    """``nosniff`` and ``no-referrer`` on every response, whatever served it.

    Pure ASGI rather than a ``@app.middleware("http")`` function so it cannot
    come between a stream and its client: this only edits the header frame on
    its way out and never touches the body.

    Both headers are per *response* rather than per page, which is why they are
    here and the policy is not. ``nosniff`` matters most for the responses that
    are not HTML — the first-run app serves prose as ``text/plain``, and content
    sniffing is precisely the mechanism that would let a browser decide
    otherwise. ``no-referrer`` keeps a run id out of the ``Referer`` of anything
    a page's own links reach.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
            await send(message)

        await self._app(scope, receive, _send)
