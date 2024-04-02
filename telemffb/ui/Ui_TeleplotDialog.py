from PyQt5.QtCore import QCoreApplication, QMetaObject, QRect, Qt
from PyQt5.QtWidgets import QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QSpacerItem, QSplitter, QWidget, QLineEdit, QPushButton


class Ui_TeleplotDialog(object):
    def setupUi(self, TeleplotDialog):
        if not TeleplotDialog.objectName():
            TeleplotDialog.setObjectName(u"TeleplotDialog")
        TeleplotDialog.resize(270, 392)
        self.layoutWidget = QWidget(TeleplotDialog)
        self.layoutWidget.setObjectName(u"layoutWidget")
        self.layoutWidget.setGeometry(QRect(7, 10, 258, 296))
        self.gridLayout = QGridLayout(self.layoutWidget)
        self.gridLayout.setObjectName(u"gridLayout")
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        self.label_4 = QLabel(self.layoutWidget)
        self.label_4.setObjectName(u"label_4")

        self.gridLayout.addWidget(self.label_4, 5, 0, 1, 1)

        self.tb_port = QLineEdit(self.layoutWidget)
        self.tb_port.setObjectName(u"tb_port")

        self.gridLayout.addWidget(self.tb_port, 2, 0, 1, 1, Qt.AlignLeft)

        self.label_3 = QLabel(self.layoutWidget)
        self.label_3.setObjectName(u"label_3")

        self.gridLayout.addWidget(self.label_3, 3, 0, 1, 1)

        self.label_2 = QLabel(self.layoutWidget)
        self.label_2.setObjectName(u"label_2")

        self.gridLayout.addWidget(self.label_2, 1, 0, 1, 1)

        self.label = QLabel(self.layoutWidget)
        self.label.setObjectName(u"label")

        self.gridLayout.addWidget(self.label, 0, 0, 1, 1)

        self.tb_vars = QPlainTextEdit(self.layoutWidget)
        self.tb_vars.setObjectName(u"tb_vars")

        self.gridLayout.addWidget(self.tb_vars, 6, 0, 1, 1)

        self.splitter = QSplitter(self.layoutWidget)
        self.splitter.setObjectName(u"splitter")
        self.splitter.setOrientation(Qt.Horizontal)
        self.label_6 = QLabel(self.splitter)
        self.label_6.setObjectName(u"label_6")
        self.splitter.addWidget(self.label_6)
        self.pb_Select = QPushButton(self.splitter)
        self.pb_Select.setObjectName(u"pb_Select")
        self.splitter.addWidget(self.pb_Select)

        self.gridLayout.addWidget(self.splitter, 4, 0, 1, 1)

        self.label_5 = QLabel(TeleplotDialog)
        self.label_5.setObjectName(u"label_5")
        self.label_5.setGeometry(QRect(7, 310, 256, 26))
        self.layoutWidget1 = QWidget(TeleplotDialog)
        self.layoutWidget1.setObjectName(u"layoutWidget1")
        self.layoutWidget1.setGeometry(QRect(10, 350, 251, 25))
        self.horizontalLayout = QHBoxLayout(self.layoutWidget1)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalLayout.setContentsMargins(0, 0, 0, 0)
        self.pb_clear = QPushButton(self.layoutWidget1)
        self.pb_clear.setObjectName(u"pb_clear")

        self.horizontalLayout.addWidget(self.pb_clear, 0, Qt.AlignLeft)

        self.horizontalSpacer_2 = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout.addItem(self.horizontalSpacer_2)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout.addItem(self.horizontalSpacer)

        self.buttonBox = QDialogButtonBox(self.layoutWidget1)
        self.buttonBox.setObjectName(u"buttonBox")
        self.buttonBox.setOrientation(Qt.Horizontal)
        self.buttonBox.setStandardButtons(QDialogButtonBox.Cancel|QDialogButtonBox.Save)

        self.horizontalLayout.addWidget(self.buttonBox, 0, Qt.AlignRight)


        self.retranslateUi(TeleplotDialog)

        QMetaObject.connectSlotsByName(TeleplotDialog)
    # setupUi

    def retranslateUi(self, TeleplotDialog):
        TeleplotDialog.setWindowTitle(QCoreApplication.translate("TeleplotDialog", u"Teleplot Setup", None))
        self.label_4.setText(QCoreApplication.translate("TeleplotDialog", u"List:", None))
        self.label_3.setText(QCoreApplication.translate("TeleplotDialog", u"Space separated list of Telemetry variables", None))
        self.label_2.setText(QCoreApplication.translate("TeleplotDialog", u"Port:", None))
        self.label.setText(QCoreApplication.translate("TeleplotDialog", u"Open a browser to teleplot.fr, record port number", None))
        self.label_6.setText(QCoreApplication.translate("TeleplotDialog", u"or select from active: ", None))
        self.pb_Select.setText(QCoreApplication.translate("TeleplotDialog", u"Select...", None))
        self.label_5.setText(QCoreApplication.translate("TeleplotDialog", u"To stop sending teleplot data,\n"
"clear the boxes and select OK", None))
        self.pb_clear.setText(QCoreApplication.translate("TeleplotDialog", u"Clear", None))