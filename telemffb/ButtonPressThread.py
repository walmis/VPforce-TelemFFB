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

from PyQt6.QtCore import QThread, pyqtSignal

import telemffb.globals as G
from telemffb.hw.ffb_rhino import HapticEffect


def wait_for_button_press(target_device=None, timeout=5.0, on_tick=None) -> int:
    """The first button newly pressed within ``timeout`` seconds, or 0.

    Watches this instance's device together with the buttons reported over
    IPC: every child's (the master's button list) or, with
    ``target_device``, that child's alone.  A button already held when the
    wait starts does not count.  ``on_tick`` is given the whole seconds
    left, about ten times a second.
    """
    def own():
        report = HapticEffect.get_device_input()
        return set(report.getPressedButtons()) if report is not None else set()

    def reported():
        if target_device is not None:
            return set(G.child_buttons.get(target_device, []))
        return set(G.master_buttons)

    held_own, held_reported = own(), reported()
    start = time.time()
    while time.time() - start < timeout:
        if on_tick is not None:
            on_tick(int(timeout - (time.time() - start)))
        for pressed, held in ((own(), held_own), (reported(), held_reported)):
            for button in pressed - held:
                return button
        time.sleep(0.1)
    return 0


class ButtonPressThread(QThread):
    button_pressed = pyqtSignal(str, int)

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
