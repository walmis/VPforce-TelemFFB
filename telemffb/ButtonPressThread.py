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


import time
from typing import Union

from PyQt6.QtCore import QThread, pyqtSignal

import telemffb.globals as G
from telemffb.hw.button_refs import format_button_ref
from telemffb.hw.button_state import role_key
from telemffb.hw.ffb_rhino import HapticEffect


def wait_for_button_press(target_device=None, timeout=5.0, on_tick=None) -> Union[int, str]:
    """The first button newly pressed within ``timeout`` seconds, or 0.

    The bare device is this instance's own or, with ``target_device`` (a
    child role, when the master binds for that child's scope), that
    child's device as reported over IPC.  A press on it returns the bare
    button number.  Every other device in ``G.button_states`` (generic
    controllers, the other FFB devices, this instance's own device when
    the target is a child) is watched too; a press there returns the
    qualified id ``<key>:<n>``.  The bare device also appears in
    ``G.button_states``, so its key is left out of the qualified path.

    A button already held when the wait starts does not count.  When
    several go down in the same poll, the bare numbers win over qualified
    ids, then the lowest number, then the lowest key.  ``on_tick`` is given
    the whole seconds left, about ten times a second.
    """
    bare_role = target_device or G.device_type

    def bare():
        if bare_role == G.device_type:
            report = HapticEffect.get_device_input()
            return set(report.getPressedButtons()) if report is not None else set()
        return set(G.child_buttons.get(bare_role, []))

    def others():
        # re-read each poll: a key is first known once its device has published
        excluded = role_key(bare_role)
        return {key: pressed for key, pressed in list(G.button_states.items())
                if key != excluded}

    held_bare, held_others = bare(), others()
    start = time.time()
    while time.time() - start < timeout:
        if on_tick is not None:
            on_tick(int(timeout - (time.time() - start)))
        new = bare() - held_bare
        if new:
            return min(new)
        candidates = [(number, key)
                      for key, pressed in others().items()
                      for number in pressed - held_others.get(key, frozenset())]
        if candidates:
            number, key = min(candidates)
            return format_button_ref(key, number)
        time.sleep(0.1)
    return 0


class ButtonPressThread(QThread):
    #: (setting name, bare button number, qualified id string, or 0)
    button_pressed = pyqtSignal(str, object)

    def __init__(self, button_obj, target_device, timeout=5):
        super(ButtonPressThread, self).__init__()
        self.button_obj = button_obj
        self.button_name = button_obj.objectName().replace('pb_', '')
        self.target_device = target_device
        self.timeout = timeout

    def run(self):
        self.button_pressed.emit(self.button_name, wait_for_button_press(
            self.target_device, self.timeout,
            on_tick=lambda left: self.button_obj.setText(f"Push a button! {left}..")))
