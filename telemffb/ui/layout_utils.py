#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""Layout helpers."""
from PyQt6.QtWidgets import QWidget


def invalidate_ancestor_layouts(widget: QWidget) -> None:
    """Tell every layout above ``widget`` that its size has changed,
    innermost first, up to the window.

    Showing or hiding a widget deep inside nested layouts ought to do this
    on its own, and mostly does - but the Application Status box's height
    is held by a layout several levels up that goes on reporting its old
    size hint, so the box keeps space for something that has gone (or the
    window keeps it, on the Hide tab). Asking directly is cheap and only
    done when something actually changed.
    """
    ancestor = widget.parentWidget()
    while ancestor is not None:
        # QWidget.layout(ancestor), not ancestor.layout(): MainWindow keeps an
        # attribute of that name, which hides the method on the instance.
        layout = QWidget.layout(ancestor)
        if layout is not None:
            layout.invalidate()
        ancestor.updateGeometry()
        if ancestor.isWindow():
            break
        ancestor = ancestor.parentWidget()
