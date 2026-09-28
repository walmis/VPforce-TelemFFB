"""DirectLink's messages as the user sees them: with its download address a
live link, at startup and in the System Settings dialog alike."""
import html

from PyQt6 import QtGui, QtWidgets

from telemffb.hw.ffb_dinput import BRIDGE_DOWNLOAD_LOCATION
from telemffb.ui.theme.tokens import LINK_BLUE_DARK, LINK_BLUE_LIGHT


def with_download_link(text: str) -> str:
    """Rich-text form of a message that names DirectLink's address, with
    the address as a live link.  The messages themselves stay plain text:
    they are also logged and asserted on, and a QLabel or QMessageBox in
    rich-text mode is the only place the link can be clicked."""
    url = BRIDGE_DOWNLOAD_LOCATION
    # The app stylesheet's link color is one purple for both themes, and it
    # all but disappears on the dark one.  The anchor is colored inline for
    # the theme in use, which leaves the stylesheet alone.
    app = QtWidgets.QApplication.instance()
    window = app.palette().color(QtGui.QPalette.ColorRole.Window) if app else None
    color = LINK_BLUE_DARK if window is not None and window.lightness() < 128 else LINK_BLUE_LIGHT
    link = (f'<a href="{html.escape(url, quote=True)}" style="color: {color}">'
            f'{html.escape(url)}</a>')
    return html.escape(text).replace('\n', '<br>').replace(html.escape(url), link)
