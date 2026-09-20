"""Tests for the theme tokens / rendered-stylesheet template.

Guards against the two failure modes a QSS-from-tokens refactor invites:
an unfilled `$placeholder` left in the rendered output, and a template
edit that unbalances braces (Qt just silently mis-styles the app when
that happens - no exception).
"""
import re

import pytest

from telemffb.ui.theme import tokens
from styles import DARK_MODE_STYLESHEET, LIGHT_MODE_STYLESHEET, render_stylesheet


@pytest.mark.parametrize("mode_tokens", [tokens.DARK, tokens.LIGHT])
def test_render_stylesheet_succeeds(mode_tokens):
    rendered = render_stylesheet(mode_tokens)
    assert isinstance(rendered, str)
    assert rendered.strip()


@pytest.mark.parametrize("rendered", [DARK_MODE_STYLESHEET, LIGHT_MODE_STYLESHEET])
def test_no_unfilled_placeholders(rendered):
    # string.Template placeholders look like $name or ${name}; none should
    # survive a real render.
    assert not re.search(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?", rendered)


@pytest.mark.parametrize("rendered", [DARK_MODE_STYLESHEET, LIGHT_MODE_STYLESHEET])
def test_braces_are_balanced(rendered):
    assert rendered.count("{") == rendered.count("}")


def test_dark_and_light_are_distinct():
    assert DARK_MODE_STYLESHEET != LIGHT_MODE_STYLESHEET
