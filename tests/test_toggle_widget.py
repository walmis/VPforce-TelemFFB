"""The toggle has to read at the size it is actually drawn, on both themes.

Two failures motivated the rework, and these guard against their return:

  * the colours were hardcoded around a dark background, so on the light
    theme the off-track composited to a pale lilac with the white handle at
    1.7:1 against it - all but invisible;
  * the handle was 14px across in a 12px bar and ran past both ends of it,
    with its shape carried by a three-stop radial gradient that has nowhere
    to render in 14 pixels.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QAbstractAnimation, QRectF, Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from telemffb.custom_widgets import Toggle, vpf_purple

pytestmark = [pytest.mark.unit]

DARK_WINDOW = QColor(53, 53, 53)      # what _apply_dark_mode_palette sets
LIGHT_WINDOW = QColor("#efefef")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def themed(app, window):
    toggle = Toggle()
    palette = QPalette(toggle.palette())
    palette.setColor(QPalette.ColorRole.Window, window)
    toggle.setPalette(palette)
    return toggle


def relative_luminance(c: QColor):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return (0.2126 * channel(c.red()) + 0.7152 * channel(c.green())
            + 0.0722 * channel(c.blue()))


def contrast(a: QColor, b: QColor):
    la, lb = relative_luminance(a), relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


class TestGeometry:
    """The handle stays in its track, at whatever size the track is drawn.

    These ask the widget where it puts things rather than recomputing it -
    an independent copy of the arithmetic would keep passing after the real
    geometry changed underneath it.
    """

    @pytest.mark.parametrize("position", [0.0, 0.25, 0.5, 0.75, 1.0])
    def test_the_handle_never_leaves_the_track(self, app, position):
        toggle = Toggle()
        track, handle_r, centre = toggle._geometry(position)
        assert centre.x() - handle_r >= track.left() - 0.01, position
        assert centre.x() + handle_r <= track.right() + 0.01, position
        assert centre.y() - handle_r >= track.top() - 0.01, position
        assert centre.y() + handle_r <= track.bottom() + 0.01, position

    def test_the_handle_is_inset_not_straddling(self, app):
        """The old handle cleared the bar by one pixel, which reads as
        neither inside it nor proud of it."""
        toggle = Toggle()
        track, handle_r, _ = toggle._geometry(0.0)
        assert track.height() - 2 * handle_r == pytest.approx(
            2 * Toggle.HANDLE_INSET)
        assert Toggle.HANDLE_INSET >= 2

    def test_the_handle_actually_travels(self, app):
        toggle = Toggle()
        _, _, off = toggle._geometry(0.0)
        _, _, on = toggle._geometry(1.0)
        assert on.x() > off.x(), "the handle does not move"

    def test_the_track_fits_inside_the_widget(self, app):
        """The margin is what gives the shadow and focus ring somewhere to
        land."""
        toggle = Toggle()
        track, _, _ = toggle._geometry()
        assert QRectF(toggle.rect()).contains(track)

    def test_the_whole_widget_is_clickable(self, app):
        """Contents margins used to shave 8px off each side of the hit area
        while the track spans the full width."""
        toggle = Toggle()
        assert toggle.contentsRect() == toggle.rect()

    def test_the_widget_is_not_much_bigger_than_what_it_draws(self, app):
        """The original wasted half its area: a 22x12 bar rattling around
        inside a 45x30 widget.  The margin is for the shadow and focus ring
        and should stay about that big.

        Stated in pixels rather than against the font: the offscreen plugin
        used by this suite loads no fonts, so its metrics are meaningless.
        """
        toggle = Toggle()
        assert toggle.width() == Toggle.TRACK_W + 2 * Toggle.MARGIN_X
        assert toggle.height() == Toggle.TRACK_H + 2 * Toggle.MARGIN_Y
        assert Toggle.MARGIN_X <= 4 and Toggle.MARGIN_Y <= 6

    def test_the_track_stays_a_sensible_size_for_a_9pt_ui(self, app):
        """43x22 beside a 9pt label read as comical; this is the bound that
        judgement settled on."""
        assert 14 <= Toggle.TRACK_H <= 20
        assert 26 <= Toggle.TRACK_W <= 38


class TestFollowsTheTheme:
    def test_the_off_track_differs_between_themes(self, app):
        dark = themed(app, DARK_WINDOW)._colors()['off']
        light = themed(app, LIGHT_WINDOW)._colors()['off']
        assert dark != light, "the track is not derived from the palette"

    @pytest.mark.parametrize("window", [DARK_WINDOW, LIGHT_WINDOW])
    def test_the_off_track_separates_from_the_page(self, app, window):
        """It used to sit at 1.20:1 on dark and 1.45:1 on light - a ball
        floating in space rather than a switch in a slot."""
        colors = themed(app, window)._colors()
        assert contrast(colors['off'], window) >= 2.0

    @pytest.mark.parametrize("window", [DARK_WINDOW, LIGHT_WINDOW])
    def test_the_handle_reads_against_the_off_track(self, app, window):
        colors = themed(app, window)._colors()
        assert contrast(colors['handle'], colors['off']) >= 2.5

    @pytest.mark.parametrize("window", [DARK_WINDOW, LIGHT_WINDOW])
    def test_the_handle_reads_against_the_on_track(self, app, window):
        """The old handle was the same colour as the checked track - 1.00:1
        before the highlight partly rescued it."""
        colors = themed(app, window)._colors()
        assert contrast(colors['handle'], colors['accent']) >= 3.0

    def test_the_accent_is_the_brand_purple_on_both_themes(self, app):
        """Lifting it for dark suits text, where thin strokes need help; a
        filled track just desaturates."""
        for window in (DARK_WINDOW, LIGHT_WINDOW):
            assert themed(app, window)._colors()['accent'] == QColor(vpf_purple)

    def test_the_accent_can_still_be_overridden(self, app):
        assert Toggle(checked_color="#3388ff")._colors()['accent'] == QColor("#3388ff")


class TestSliding:
    """Only a toggle the user flipped slides.

    The aircraft settings page rebuilds every row on each edit and sets the
    switches from the stored values; animating those meant the whole page
    played a wave of slides whenever anything changed.
    """

    def test_a_click_animates(self, app):
        toggle = Toggle()
        assert toggle._animation.duration() == Toggle.SLIDE_MS
        toggle.click()
        assert toggle._animation.state() == QAbstractAnimation.State.Running
        assert toggle._animation.endValue() == 1.0

    def test_the_space_bar_animates_too(self, app):
        """Keyboard toggling is the user as much as the mouse is; both come
        through nextCheckState."""
        toggle = Toggle()
        toggle.nextCheckState()
        assert toggle._animation.state() == QAbstractAnimation.State.Running

    @pytest.mark.parametrize("setter", [
        lambda t: t.setChecked(True),
        lambda t: t.setCheckState(Qt.CheckState.Checked),
    ])
    def test_the_app_setting_it_snaps(self, app, setter):
        toggle = Toggle()
        setter(toggle)
        assert toggle._animation.state() == QAbstractAnimation.State.Stopped
        assert toggle.handle_position == pytest.approx(1.0), "it should be there already"

    def test_a_rebuilt_page_leaves_no_switch_mid_slide(self, app):
        """What the settings page does: build the row, then set its value."""
        for value in (True, False, True):
            toggle = Toggle()
            toggle.setChecked(value)
            assert toggle.handle_position == pytest.approx(1.0 if value else 0.0)
            assert toggle._animation.state() == QAbstractAnimation.State.Stopped

    def test_a_click_still_settles_at_the_end(self, app):
        toggle = Toggle()
        toggle.click()
        toggle._animation.setCurrentTime(Toggle.SLIDE_MS)
        assert toggle.handle_position == pytest.approx(1.0)
        toggle.click()
        toggle._animation.setCurrentTime(Toggle.SLIDE_MS)
        assert toggle.handle_position == pytest.approx(0.0)

    def test_setting_the_position_repaints(self, app):
        toggle = Toggle()
        toggle.handle_position = 0.5
        assert toggle.handle_position == 0.5
