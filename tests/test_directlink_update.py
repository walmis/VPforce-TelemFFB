"""The rule that decides whether the DirectLink site's version is offered."""
import pytest

from telemffb.ui.updates import directlink_update_available

pytestmark = pytest.mark.unit


def test_newer_version_offered():
    assert directlink_update_available("0.9.6", "0.9.7.0", "")


def test_same_version_padded_differently_not_offered():
    assert not directlink_update_available("0.9.6", "0.9.6.0", "")


def test_older_site_version_not_offered():
    assert not directlink_update_available("0.9.7", "0.9.6.0", "")


def test_compared_numerically_not_lexically():
    assert directlink_update_available("0.9.6", "0.9.10.0", "")


def test_dismissed_version_not_offered_again():
    assert not directlink_update_available("0.9.6", "0.9.7.0", "0.9.7.0")


def test_dismissal_of_an_older_version_does_not_hide_a_newer_one():
    assert directlink_update_available("0.9.6", "0.9.8.0", "0.9.7.0")


def test_unknown_installed_version_offers_nothing():
    assert not directlink_update_available("", "0.9.7.0", "")
    assert not directlink_update_available("?", "0.9.7.0", "")
