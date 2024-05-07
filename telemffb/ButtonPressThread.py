from PyQt5.QtCore import QThread, pyqtSignal
import time


class ButtonPressThread(QThread):
    button_pressed = pyqtSignal(str, int)

    def __init__(self, device, button_obj, timeout=5):
        button_name = button_obj.objectName().replace('pb_', '')
        self.button_obj = button_obj
        super(ButtonPressThread, self).__init__()
        self.device = device
        self.button_name = button_name
        self.timeout = timeout
        self.prev_button_state = None

    def run(self):
        start_time = time.time()
        emit_sent = 0
        input_data = self.device.device.get_input()

        initial_buttons = input_data.getPressedButtons()

        while not emit_sent and time.time() - start_time < self.timeout:
            input_data = self.device.device.get_input()
            current_btns = set(input_data.getPressedButtons())
            countdown = int(self.timeout - (time.time() - start_time))
            self.button_obj.setText(f"Push a button! {countdown}..")
            # Check for new button press
            for btn in current_btns:
                if btn not in initial_buttons:
                    self.button_pressed.emit(self.button_name, btn)
                    emit_sent = 1

            self.prev_button_state = current_btns
            time.sleep(0.1)

        # Emit signal for timeout with value 0
        if not emit_sent:
            self.button_pressed.emit(self.button_name, 0)