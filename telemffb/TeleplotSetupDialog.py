import telemffb.globals as G
import telemffb.utils as utils


from PyQt5.QtCore import QRegExp
from PyQt5.QtGui import QIntValidator, QRegExpValidator
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QMessageBox, QPushButton, QVBoxLayout

from .ui.Ui_TeleplotDialog import Ui_TeleplotDialog

class TeleplotSetupDialog(QDialog, Ui_TeleplotDialog):

    def __init__(self, parent=None):
        super(TeleplotSetupDialog, self).__init__(parent)

        if G.args.teleplot is None:
            self.telem_port = ''
        elif isinstance(G.args.teleplot, str):
            if ':' in G.args.teleplot:
                self.telem_port = G.args.teleplot.split(':')[1]
            else:
                self.telem_port = G.args.teleplot

        if G.args.plot is None:
            G.args.plot = []
        self.telem_vars = ' '.join(G.args.plot)

        self.setupUi(self)
        self.retranslateUi(self)
        self.parent = parent
        int_validator = QIntValidator()
        self.tb_port.setValidator(int_validator)
        self.buttonBox.accepted.connect(self.save_teleplot)
        self.buttonBox.rejected.connect(self.close)
        self.pb_clear.clicked.connect(self.clear_form)
        self.pb_Select.clicked.connect(self.select_active_telemetry)
        self.tb_port.setText(self.telem_port)
        self.tb_vars.setPlainText(str(self.telem_vars))
        self.telem_data = parent.lbl_telem_data.text()

    class KeySelectionDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Select Keys")
            self.parent = parent
            layout = QVBoxLayout(self)
            self.list_widget = QListWidget()
            self.list_widget.setSelectionMode(QListWidget.MultiSelection)  # Allow multiple selections
            self.list_widget.addItems(self.get_active_keys())
            refresh_button = QPushButton()
            refresh_button.setText("Refresh Keys")
            layout.addWidget(refresh_button)
            layout.addWidget(self.list_widget)
            button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
            refresh_button.clicked.connect(self.refresh_keys)
            button_box.accepted.connect(self.populate_keys)
            button_box.rejected.connect(self.reject)
            button_box.button(QDialogButtonBox.Reset).clicked.connect(self.clearSelection)
            layout.addWidget(button_box)

        def populate_keys(self):
            keys = self.selectedKeys()
            str = ''
            for k in keys:
                str = str + f"{k} "
            self.parent.tb_vars.setPlainText(str.rstrip(" "))
            self.accept()
        def refresh_keys(self):
            self.list_widget.addItems(self.get_active_keys())
        def get_active_keys(self):
            text = self.parent.parent.lbl_telem_data.text()
            keys = [line.split(':')[0].strip() for line in text.split('\n') if line.strip()]
            return keys
        def selectedKeys(self):
            return [item.text() for item in self.list_widget.selectedItems()]

        def clearSelection(self):
            self.list_widget.clearSelection()
    def select_active_telemetry(self):
        self._telem_selection_window = self.KeySelectionDialog(parent=self)
        self._telem_selection_window.exec_()
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
                G.args.teleplot = str(self.tb_port.text())
                self.accept()

    def clear_form(self):
        self.tb_port.clear()
        self.tb_vars.clear()

    def validate_text(self):
        regex_string = r"[a-zA-Z_][a-zA-Z0-9_ ]*"
        current_text = self.tb_vars.toPlainText()
        validator = QRegExpValidator(QRegExp(regex_string))
        pos = 0
        state, valid_text, pos = validator.validate(current_text, pos)
        if self.tb_port.text() == '':
            if current_text == '':
                # remove all teleplot
                return True
            elif current_text != '':
                QMessageBox.warning(self, "Error", "Please enter a port number or remove the telemetry variables to stop sending")
                return False

        if current_text == '':
            if self.tb_port.text() != '':
                QMessageBox.warning(self, "Error", "Please enter telemetry variables to monitor or remove the port to stop sending")
                return False

        if state == QRegExpValidator.Acceptable or current_text == '':
            return True
        else:
            QMessageBox.warning(self, "Error", "Please only enter valid variable characters")
            return False