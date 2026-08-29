"""The pedal axis-mismatch check is a VPforce firmware contract.

A DIY VPforce pedal set can have either axis configured as the active
one, and the inactive axis reports a clean 0 - so motion on Y while X
sits at exactly 0 means the device is set up backwards, and the fix is
VPConfigurator.  Neither half holds for a third-party DirectInput
device: its unused axes may idle with noise, and its remedy is the FFB
Axis pulldown.  The check therefore applies only to VPforce hardware.
"""
import unittest.mock as mock

import pytest

pytest.importorskip("PyQt6")

import telemffb.globals as G

pytestmark = [pytest.mark.unit]


def _pedals_instance(spring_mode="STATIC"):
    from tests.test_ffb_tap import TestTapSpringMode
    case, inst = TestTapSpringMode()._make_instance(spring_mode)
    inst._telem_data["FFBType"] = "pedals"
    return inst


def _verify(inst, axes, di_guid):
    with mock.patch.object(G, 'device_di_guid', di_guid, create=True):
        with mock.patch.object(type(inst), '_get_device_axes',
                               staticmethod(lambda: axes)):
            inst.verify_pedal_axis()
    return inst._telem_data.get('error', '')


def test_a_backwards_vpforce_pedal_is_flagged():
    error = _verify(_pedals_instance(), axes=(0.0, 0.4), di_guid=None)
    assert 'Pedal axis mismatch' in error


def test_a_directinput_pedal_is_never_checked():
    """The same reading on a DirectInput device proves nothing - noise
    on an unused axis looks identical - and the advice (VPConfigurator)
    does not exist for third-party hardware."""
    error = _verify(_pedals_instance(), axes=(0.0, 0.4), di_guid='{GUID}')
    assert not error


def test_a_correctly_set_up_pedal_stays_quiet():
    error = _verify(_pedals_instance(), axes=(0.4, 0.0), di_guid=None)
    assert not error
