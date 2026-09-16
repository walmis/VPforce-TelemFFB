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

"""Application-level signals.

The home for events whose emitter and consumers should not know each
other: System Settings announces that the device configuration changed,
and whoever cares - the status panel, the aircraft settings form,
tomorrow's consumers - subscribes here.  Point-to-point calls from the
dialog were growing into an orchestrator; a new consumer now costs one
connect() instead of another hand-wired call inside save_settings.
"""

from PyQt6 import sip
from PyQt6.QtCore import QObject, pyqtSignal


class AppEvents(QObject):
    #: The stored device configuration changed (a System Settings save).
    #: Payloads are {settings key: devpath} snapshots taken before and
    #: after the save - keys are devpath_{role} for every role plus the
    #: joystick alternates (devpath_joystick_2/_3).  Emitted on every
    #: save, changed or not: identity details a path snapshot cannot see
    #: (idents, icon choices) may have changed, so consumers decide for
    #: themselves what to do.
    device_config_changed = pyqtSignal(dict, dict)


_events = None


def events() -> AppEvents:
    """The application's event hub.

    An accessor rather than a module-level instance: a parentless
    QObject dies with the QApplication, which in the app never happens
    (connections made at startup live forever) but in a test process
    QApplications come and go - callers always emit into and connect to
    the hub that is currently alive.
    """
    global _events
    if _events is None or sip.isdeleted(_events):
        _events = AppEvents()
    return _events
