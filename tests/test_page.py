"""The one page render every local app shares.

The three apps used to keep their own copies of this: three policy strings,
three ``_escape``/``_page``/``_html`` triples, and two spellings of the escape a
value needs inside a ``<script>`` block. The copies are what these tests
replace — proving the rule once is what makes it true of every page.
"""

from __future__ import annotations

import json

import pytest

from webapp.page import (
    NONCE_PLACEHOLDER,
    Grants,
    MissingPlaceholder,
    escape,
    render,
    script_json,
)

#: What each page shipped before the three copies became one, with the nonce
#: written as ``N``. Pinned as whole strings rather than as substrings: a
#: substring check passes when a refactor *adds* a grant, which is the only
#: direction that matters here.
SHIPPED = {
    Grants(script=True, style=True): (
        "default-src 'none'; script-src 'nonce-N'; style-src 'nonce-N'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    Grants(script=True, style=True, connect=True): (
        "default-src 'none'; script-src 'nonce-N'; style-src 'nonce-N'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    Grants(style=True): (
        "default-src 'none'; style-src 'nonce-N'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    ),
}


@pytest.mark.parametrize("grants", list(SHIPPED))
def test_each_grant_set_builds_the_policy_its_page_shipped_with(grants):
    assert grants.policy("N") == SHIPPED[grants]


def test_a_page_that_declares_nothing_is_granted_nothing():
    """The direction a mistake should fail in.

    A new page that says nothing about what it does gets ``default-src 'none'``
    and the three closed directives, so it can run no script, carry no style
    and reach no origin until it says it needs to.
    """
    policy = Grants().policy("N")
    assert policy.startswith("default-src 'none';")
    for granted in ("script-src", "style-src", "connect-src"):
        assert granted not in policy


@pytest.mark.parametrize("directive", ["base-uri", "form-action", "frame-ancestors"])
@pytest.mark.parametrize("grants", list(SHIPPED) + [Grants()])
def test_the_closed_directives_are_spelled_on_every_page(grants, directive):
    """None of the three falls back to ``default-src``.

    A policy can read as total and still leave a page framable, which is why
    they are written out rather than left to the fallback.
    """
    assert f"{directive} 'none'" in grants.policy("N")


def test_the_server_side_escape_covers_quotes():
    """Every call site lands in text position today; that is not the guarantee.

    An escape that is only adequate where it happens to be called is one
    interpolation away from not being adequate, so the helper is correct in
    attribute position too.
    """
    escaped = escape("\"><img src=x onerror=alert(1)>'")
    for character in ("<", ">", '"', "'"):
        assert character not in escaped


def test_a_script_value_cannot_close_its_own_block():
    """The stored-input XSS this module exists to make unspellable.

    A value spelling ``</script>`` would end the block and everything after it
    would parse as HTML. It survives as data instead.
    """
    breakout = "</script><img src=x onerror=alert(1)>"
    literal = script_json({"name": breakout})
    assert "</script>" not in literal
    assert json.loads(literal)["name"] == breakout


def test_a_script_value_is_a_javascript_literal_not_escaped_markup():
    """Nothing decodes HTML entities in a script block.

    ``html.escape`` would deliver the characters ``&quot;`` where a quote was,
    which is why the two escapes are two functions and not one.
    """
    assert script_json('say "hi"') == '"say \\"hi\\""'


def test_the_nonce_is_stamped_before_the_fields_are_filled():
    """A field's value is content.

    Content that happens to spell the placeholder must come back as those
    characters rather than become a live nonce.
    """
    page = render(
        f'<style nonce="{NONCE_PLACEHOLDER}"></style><p><!--message--></p>',
        Grants(style=True),
        message=escape(NONCE_PLACEHOLDER),
    )
    assert f"<p>{NONCE_PLACEHOLDER}</p>" in page.html


def test_every_render_gets_a_fresh_nonce():
    """A nonce reused across responses is not a nonce."""
    template = f'<style nonce="{NONCE_PLACEHOLDER}"></style>'
    first = render(template, Grants(style=True))
    second = render(template, Grants(style=True))
    assert first.html != second.html
    assert first.csp != second.csp


def test_the_page_and_its_policy_authorise_each_other():
    """The nonce in the markup is the nonce the header names."""
    page = render(f'<style nonce="{NONCE_PLACEHOLDER}"></style>', Grants(style=True))
    nonce = page.html.split('nonce="')[1].split('"')[0]
    assert f"style-src 'nonce-{nonce}'" in page.csp


def test_a_field_whose_placeholder_is_missing_raises():
    """The template and the caller disagree about what the page holds.

    Serving the page with the value silently dropped is the worse of the two
    outcomes: the first-run app's report page *is* its payload, and an empty one
    renders as a report of nothing rather than as a failure.
    """
    with pytest.raises(MissingPlaceholder):
        render("<p>no slot here</p>", Grants(), message="anything")
