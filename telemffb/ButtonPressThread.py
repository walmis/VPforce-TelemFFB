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


from PyQt6.QtCore import QThread, pyqtSignal
import time
import telemffb.globals as G


class ButtonPressThread(QThread):
    button_pressed = pyqtSignal(str, int)

    def __init__(self, device, button_obj, target_device, timeout=5):
        button_name = button_obj.objectName().replace('pb_', '')
        self.button_obj = button_obj
        super(ButtonPressThread, self).__init__()
        self.device = device
        self.button_name = button_name
        self.target_device = target_device
        self.timeout = timeout
        self.prev_button_state = None

    def run(self):
        start_time = time.time()
        emit_sent = 0
        input_data = self.device.device.get_input()
        if self.target_device is not None:
            # button context for non-master device..  listen to both self and device
            # inputs from IPC
            initial_self_buttons = input_data.getPressedButtons()
            initial_target_buttons = G.child_buttons.get(self.target_device, [])
            # initial_buttons = list(set(self_buttons + target_buttons))
        else:
            initial_buttons = input_data.getPressedButtons()
            initial_master_buttons = G.master_buttons

        while not emit_sent and time.time() - start_time < self.timeout:
            input_data = self.device.device.get_input()
            if self.target_device is not None:
                # Get latest results for self and target device inputs
                self_buttons = input_data.getPressedButtons()
                target_buttons = G.child_buttons.get(self.target_device, [])
                # current_buttons = list(set(self_buttons + target_buttons))
            else:
                current_buttons = set(input_data.getPressedButtons())
                current_master_buttons = set(G.master_buttons)
            countdown = int(self.timeout - (time.time() - start_time))
            self.button_obj.setText(f"Push a button! {countdown}..")
            # Check for new button press
            if self.target_device is not None:
                # check self buttons and target device buttons independently to look for new presses
                for btn in self_buttons:
                    if btn not in initial_self_buttons:
                        self.button_pressed.emit(self.button_name, btn)
                        emit_sent = 1
                for btn in target_buttons:
                    if btn not in initial_target_buttons:
                        self.button_pressed.emit(self.button_name, btn)
                        emit_sent = 1
            else:
                for btn in current_buttons:
                    if btn not in initial_buttons:
                        self.button_pressed.emit(self.button_name, btn)
                        emit_sent = 1
                for btn in current_master_buttons:
                    if btn not in initial_master_buttons:
                        self.button_pressed.emit(self.button_name, btn)
                        emit_sent = 1
            time.sleep(0.1)

        # Emit signal for timeout with value 0
        if not emit_sent:
            self.button_pressed.emit(self.button_name, 0)