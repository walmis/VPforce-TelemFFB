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


import telemffb.globals as G
import telemffb.utils as utils


from PyQt6.QtCore import QRegularExpression, Qt
from PyQt6.QtGui import QIntValidator, QRegularExpressionValidator
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QMessageBox, QPushButton, QVBoxLayout

from .ui.Ui_TeleplotDialog import Ui_TeleplotDialog

class TeleplotSetupDialog(QDialog, Ui_TeleplotDialog):

    def __init__(self, parent=None):
        super(TeleplotSetupDialog, self).__init__(parent)

        if G.args.teleplot is None:
            self.telem_port = G.system_settings.get('teleplotPort', '')
        elif isinstance(G.args.teleplot, str):
            if ':' in G.args.teleplot:
                self.telem_port = G.args.teleplot.split(':')[1]
            else:
                self.telem_port = G.args.teleplot

        if G.args.plot is None:
            G.args.plot = []
            self.telem_vars = G.system_settings.get('teleplotVars', '')
        else:
            self.telem_vars = ' '.join(G.args.plot)

        self.setupUi(self)
        self.retranslateUi(self)
        self.cb_send.setEnabled(utils.teleplot.enabled)
        self.cb_send.checkStateChanged.connect(self.cb_send_checked)
        self.pb_Save.setEnabled(False)
        self.parent = parent
        int_validator = QIntValidator()
        self.tb_port.setValidator(int_validator)
        self.pb_Save.clicked.connect(self.save_teleplot)
        self.pb_Cancel.clicked.connect(self.close)
        self.pb_clear.clicked.connect(self.clear_form)
        self.pb_Select.clicked.connect(self.select_active_telemetry)
        self.telem_data = parent.lbl_telem_data.text()
        self.tb_port.textChanged.connect(self.setsendflag)
        self.tb_vars.textChanged.connect(self.setsendflag)
        self.tb_port.setText(str(self.telem_port))
        self.tb_vars.setPlainText(str(self.telem_vars))

        # Teleplot Link
        bookmarked_section =  "https://teleplot.fr"
        linkcolor = 'style="color: #c473d9;"' if G.useDarkMode else 'style="color: #ab37c8;"'
        self.label.setText(f'Click to open a browser to: <a href="{bookmarked_section}" {linkcolor}>teleplot.fr</a>')
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.label.setOpenExternalLinks(True)

    class KeySelectionDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Select Keys")
            self.parent = parent
            layout = QVBoxLayout(self)
            self.list_widget = QListWidget()
            self.list_widget.setSelectionMode(QListWidget.SelectionMode.MultiSelection)  # Allow multiple selections
            self.list_widget.addItems(self.get_active_keys())
            refresh_button = QPushButton()
            refresh_button.setText("Refresh Keys")
            layout.addWidget(refresh_button)
            layout.addWidget(self.list_widget)
            button_box = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok |
                QDialogButtonBox.StandardButton.Cancel |
                QDialogButtonBox.StandardButton.Reset
            )

            refresh_button.clicked.connect(self.refresh_keys)
            button_box.accepted.connect(self.populate_keys)
            button_box.rejected.connect(self.reject)
            button_box.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self.clearSelection)
            layout.addWidget(button_box)

        def populate_keys(self):
            keys = self.selectedKeys()
            str = ''
            for k in keys:
                str = str + f"{k} "
            self.parent.tb_vars.setPlainText(str.rstrip(" "))
            self.accept()
        def refresh_keys(self):
            self.list_widget.clear()
            self.list_widget.addItems(self.get_active_keys())
        def get_active_keys(self):
            text = self.parent.parent.lbl_telem_data.text()
            keys = [line.split(':')[0].strip() for line in text.split('\n') if line.strip()]
            if keys[0] == 'Waiting for data...':
                keys = ['Sim not running']
            return keys
        def selectedKeys(self):
            return [item.text() for item in self.list_widget.selectedItems()]

        def clearSelection(self):
            self.list_widget.clearSelection()

    def setsendflag(self):
        #allow save and send when both have text
        if (self.tb_port.text() != '' and self.tb_vars.toPlainText() !=''):
            self.cb_send.setEnabled(True)
            self.pb_Save.setEnabled(True)
        else:
            self.cb_send.setEnabled(False)
            self.cb_send.setChecked(False)
            self.pb_Save.setEnabled(False)

        # allow save when all empty
        if (self.tb_port.text() == '' and self.tb_vars.toPlainText() ==''):
            self.pb_Save.setEnabled(True)

    def cb_send_checked(self):
        utils.teleplot.enabled = self.cb_send.isChecked()

    def select_active_telemetry(self):
        self._telem_selection_window = self.KeySelectionDialog(parent=self)
        self._telem_selection_window.exec()
        pass
    def save_teleplot(self):
        if self.validate_text():
            if self.tb_port == '':
                G.args.plot = []
                self.accept()
            else:
                address = f"teleplot.fr:{str(self.tb_port.text())}"
                utils.teleplot.configure(address)
                G.args.plot = self.tb_vars.toPlainText().split()
                G.system_settings.setValue(f"{G.device_type}/teleplotVars", self.tb_vars.toPlainText())
                G.args.teleplot = str(self.tb_port.text())
                G.system_settings.setValue(f"{G.device_type}/teleplotPort", str(self.tb_port.text()))
                self.accept()


    def clear_form(self):
        self.tb_port.clear()
        self.tb_vars.clear()
        self.cb_send.setChecked(False)
        self.cb_send.setEnabled(False)

    def validate_text(self):
        regex_string = r"[a-zA-Z_][a-zA-Z0-9_ ]*"
        current_text = self.tb_vars.toPlainText()
        regex = QRegularExpression(regex_string)
        validator = QRegularExpressionValidator(regex)
        pos = 0
        state, valid_text, pos = validator.validate(current_text, pos)

        # should be unnecessary with save button disabling:
        # if self.tb_port.text() == '':
        #     if current_text == '':
        #         # remove all teleplot
        #         return True
        #     elif current_text != '':
        #         QMessageBox.warning(self, "Error", "Please enter a port number or remove the telemetry variables to stop sending")
        #         return False
        #
        # if current_text == '':
        #     if self.tb_port.text() != '':
        #         QMessageBox.warning(self, "Error", "Please enter telemetry variables to monitor or remove the port to stop sending")
        #         return False

        if state == QRegularExpressionValidator.State.Acceptable or current_text == '':
            return True
        else:
            QMessageBox.warning(self, "Error", "Please only enter valid variable characters")
            return False