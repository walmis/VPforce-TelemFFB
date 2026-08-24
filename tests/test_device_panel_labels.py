"""Device status-icon labels: the hardware behind each role named under
its icon (utils.device_panel_label + the DevicePanel label/flash surface).
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from telemffb.utils import device_panel_label

pytestmark = [pytest.mark.unit]


@pytest.fixture
def app():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeSettings(dict):
    def get(self, name, default=None, instance=None):
        return dict.get(self, name, default)


class TestDevicePanelLabel:
    def test_vpforce_ident_passes_through(self):
        s = FakeSettings(devident_joystick='Monster',
                         devpath_joystick=r'\\?\HID#VID_FFFF&PID_2054')
        assert device_panel_label('joystick', s) == 'Monster'

    def test_dinput_ident_cut_to_leading_words(self):
        s = FakeSettings(
            devident_joystick='Microsoft SideWinder Force Feedback 2',
            devpath_joystick='dinput:{ABCD-1234}')
        assert device_panel_label('joystick', s) == 'Microsoft'

    def test_dinput_selector_marker_is_not_the_name(self):
        """The selector lists DI devices as '[DI] name' and the ident was
        stored that way - the label must skip the marker, not show it."""
        s = FakeSettings(
            devident_joystick='[DI] Microsoft SideWinder Force Feedback 2',
            devpath_joystick='dinput:{ABCD-1234}')
        assert device_panel_label('joystick', s) == 'Microsoft'

    def test_dinput_short_words_accumulate(self):
        s = FakeSettings(devident_joystick='Moza AB9 Base',
                         devpath_joystick='dinput:{ABCD-1234}')
        assert device_panel_label('joystick', s) == 'Moza AB9 Base'

    def test_overlong_ident_truncated_with_ellipsis(self):
        s = FakeSettings(devident_joystick='My Extremely Verbose Stick Name',
                         devpath_joystick=r'\\?\HID#VID_FFFF&PID_2054')
        label = device_panel_label('joystick', s)
        assert len(label) == 14
        assert label.endswith('…')

    def test_unknown_identity_reads_empty(self):
        assert device_panel_label('joystick', FakeSettings()) == ''


class TestWidgetLabelAndFlash:
    def _widget(self, app):
        from telemffb.DevicePanel import DeviceIconWidget
        return DeviceIconWidget('joystick', 'no_such_icon.png')

    def test_label_set_and_fallback(self, app):
        w = self._widget(app)
        w.set_label('Monster')
        assert w.text_label.text() == 'Monster'
        w.set_label('')
        assert w.text_label.text() == 'Joystick'   # generic role fallback

    def test_flash_is_finite_and_restores_the_status_tint(self, app):
        w = self._widget(app)
        steps = len(w.FLASH_CURVE)
        w.flash(pulses=1)
        assert w._flash_state == steps - 1         # first step already ran
        assert w._flash_timer.isActive()
        for _ in range(steps):                     # run the ramp out
            w._flash_step()
        assert w._flash_state == 0
        assert not w._flash_timer.isActive()

    def test_flash_never_touches_the_opacity_effect(self, app):
        """Animating the graphics-effect opacity over the scaled pixmap
        misrendered the icon (wrong size/position) for the flash's
        duration - the flash must blink via pixmap tint only."""
        w = self._widget(app)
        before = w.icon_opacity.opacity()
        w.flash()
        assert w.icon_fade.state() == w.icon_fade.State.Stopped
        assert w.icon_opacity.opacity() == before

    def test_error_pulse_takes_precedence_over_flash(self, app):
        w = self._widget(app)
        w._start_border_pulse()
        w.flash()
        assert w._flash_state == 0                 # refused while pulsing
        w.flash(pulses=2)
        assert w._flash_state == 0
        w._stop_border_pulse()

    def test_border_pulse_cancels_a_running_flash(self, app):
        w = self._widget(app)
        w.flash(pulses=3)
        assert w._flash_timer.isActive()
        w._start_border_pulse()
        assert not w._flash_timer.isActive()
        w._stop_border_pulse()

    def test_text_label_renders_without_a_graphics_effect(self, app):
        """The periodic status restyles left an opacity-effect label with a
        stale blank render until an outside repaint; the text must be a
        plain label (shown/hidden, size retained so the icon never shifts)."""
        w = self._widget(app)
        assert w.text_label.graphicsEffect() is None
        assert w.text_label.sizePolicy().retainSizeWhenHidden()
        assert not w.text_label.isVisibleTo(w)     # hidden until hover/active
        w.set_active(True)
        assert w.text_label.isVisibleTo(w)

    def test_panel_reports_label_change(self, app):
        from telemffb.DevicePanel import DeviceIconPanel
        panel = DeviceIconPanel()
        panel.set_devices(['joystick'])
        assert panel.set_device_label('joystick', 'Monster') is True
        assert panel.set_device_label('joystick', 'Monster') is False
        assert panel.set_device_label('joystick', '') is True   # back to role
        assert panel.set_device_label('joystick', '') is False
