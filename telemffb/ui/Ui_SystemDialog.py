# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'system_settings.ui'
#
# Created by: PyQt5 UI code generator 5.15.10
#
# WARNING: Any manual changes made to this file will be lost when pyuic5 is
# run again.  Do not edit this file unless you know what you are doing.


from PyQt5 import QtCore, QtGui, QtWidgets


class Ui_SystemDialog(object):
    def setupUi(self, SystemDialog):
        SystemDialog.setObjectName("SystemDialog")
        SystemDialog.resize(620, 680)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(SystemDialog.sizePolicy().hasHeightForWidth())
        SystemDialog.setSizePolicy(sizePolicy)
        SystemDialog.setMinimumSize(QtCore.QSize(620, 680))
        SystemDialog.setMaximumSize(QtCore.QSize(620, 680))
        self.line = QtWidgets.QFrame(SystemDialog)
        self.line.setGeometry(QtCore.QRect(9, 168, 581, 16))
        font = QtGui.QFont()
        font.setBold(False)
        font.setWeight(50)
        self.line.setFont(font)
        self.line.setLineWidth(2)
        self.line.setMidLineWidth(1)
        self.line.setFrameShape(QtWidgets.QFrame.HLine)
        self.line.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.line.setObjectName("line")
        self.buttonBox = QtWidgets.QDialogButtonBox(SystemDialog)
        self.buttonBox.setGeometry(QtCore.QRect(430, 640, 156, 23))
        self.buttonBox.setOrientation(QtCore.Qt.Horizontal)
        self.buttonBox.setStandardButtons(QtWidgets.QDialogButtonBox.Cancel|QtWidgets.QDialogButtonBox.Save)
        self.buttonBox.setObjectName("buttonBox")
        self.resetButton = QtWidgets.QPushButton(SystemDialog)
        self.resetButton.setGeometry(QtCore.QRect(21, 640, 121, 23))
        self.resetButton.setObjectName("resetButton")
        self.layoutWidget = QtWidgets.QWidget(SystemDialog)
        self.layoutWidget.setGeometry(QtCore.QRect(20, 31, 261, 137))
        self.layoutWidget.setObjectName("layoutWidget")
        self.gridLayout = QtWidgets.QGridLayout(self.layoutWidget)
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        self.gridLayout.setObjectName("gridLayout")
        self.label = QtWidgets.QLabel(self.layoutWidget)
        self.label.setObjectName("label")
        self.gridLayout.addWidget(self.label, 0, 0, 1, 1)
        self.logLevel = QtWidgets.QComboBox(self.layoutWidget)
        self.logLevel.setObjectName("logLevel")
        self.gridLayout.addWidget(self.logLevel, 0, 1, 1, 1)
        self.horizontalLayout_5 = QtWidgets.QHBoxLayout()
        self.horizontalLayout_5.setObjectName("horizontalLayout_5")
        spacerItem = QtWidgets.QSpacerItem(30, 20, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
        self.horizontalLayout_5.addItem(spacerItem)
        self.lab_logPrune = QtWidgets.QLabel(self.layoutWidget)
        self.lab_logPrune.setEnabled(False)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.lab_logPrune.sizePolicy().hasHeightForWidth())
        self.lab_logPrune.setSizePolicy(sizePolicy)
        self.lab_logPrune.setObjectName("lab_logPrune")
        self.horizontalLayout_5.addWidget(self.lab_logPrune)
        self.tb_logPrune = QtWidgets.QLineEdit(self.layoutWidget)
        self.tb_logPrune.setEnabled(False)
        self.tb_logPrune.setMaximumSize(QtCore.QSize(30, 16777215))
        self.tb_logPrune.setObjectName("tb_logPrune")
        self.horizontalLayout_5.addWidget(self.tb_logPrune)
        self.combo_logPrune = QtWidgets.QComboBox(self.layoutWidget)
        self.combo_logPrune.setEnabled(False)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.combo_logPrune.sizePolicy().hasHeightForWidth())
        self.combo_logPrune.setSizePolicy(sizePolicy)
        self.combo_logPrune.setObjectName("combo_logPrune")
        self.combo_logPrune.addItem("")
        self.combo_logPrune.addItem("")
        self.combo_logPrune.addItem("")
        self.horizontalLayout_5.addWidget(self.combo_logPrune)
        spacerItem1 = QtWidgets.QSpacerItem(40, 20, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        self.horizontalLayout_5.addItem(spacerItem1)
        self.gridLayout.addLayout(self.horizontalLayout_5, 4, 0, 1, 2)
        self.cb_logPrune = LabeledToggle(self.layoutWidget)
        self.cb_logPrune.setObjectName("cb_logPrune")
        self.gridLayout.addWidget(self.cb_logPrune, 3, 0, 1, 1)
        self.label_2 = QtWidgets.QLabel(self.layoutWidget)
        self.label_2.setObjectName("label_2")
        self.gridLayout.addWidget(self.label_2, 1, 0, 1, 1)
        self.telemTimeout = QtWidgets.QLineEdit(self.layoutWidget)
        self.telemTimeout.setObjectName("telemTimeout")
        self.gridLayout.addWidget(self.telemTimeout, 1, 1, 1, 1)
        self.ignoreUpdate = LabeledToggle(self.layoutWidget)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.ignoreUpdate.sizePolicy().hasHeightForWidth())
        self.ignoreUpdate.setSizePolicy(sizePolicy)
        self.ignoreUpdate.setObjectName("ignoreUpdate")
        self.gridLayout.addWidget(self.ignoreUpdate, 2, 0, 1, 2)
        self.line_2 = QtWidgets.QFrame(SystemDialog)
        self.line_2.setGeometry(QtCore.QRect(10, 453, 581, 16))
        font = QtGui.QFont()
        font.setBold(False)
        font.setWeight(50)
        self.line_2.setFont(font)
        self.line_2.setLineWidth(2)
        self.line_2.setMidLineWidth(1)
        self.line_2.setFrameShape(QtWidgets.QFrame.HLine)
        self.line_2.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.line_2.setObjectName("line_2")
        self.labelSystem = QtWidgets.QLabel(SystemDialog)
        self.labelSystem.setGeometry(QtCore.QRect(20, 10, 141, 16))
        font = QtGui.QFont()
        font.setBold(True)
        font.setUnderline(True)
        font.setWeight(75)
        self.labelSystem.setFont(font)
        self.labelSystem.setObjectName("labelSystem")
        self.labelSim = QtWidgets.QLabel(SystemDialog)
        self.labelSim.setGeometry(QtCore.QRect(20, 184, 151, 16))
        font = QtGui.QFont()
        font.setBold(True)
        font.setUnderline(True)
        font.setWeight(75)
        self.labelSim.setFont(font)
        self.labelSim.setObjectName("labelSim")
        self.labelOther = QtWidgets.QLabel(SystemDialog)
        self.labelOther.setGeometry(QtCore.QRect(20, 466, 181, 16))
        font = QtGui.QFont()
        font.setBold(True)
        font.setUnderline(True)
        font.setWeight(75)
        self.labelOther.setFont(font)
        self.labelOther.setObjectName("labelOther")
        self.label_8 = QtWidgets.QLabel(SystemDialog)
        self.label_8.setGeometry(QtCore.QRect(30, 486, 101, 16))
        self.label_8.setObjectName("label_8")
        self.layoutWidget1 = QtWidgets.QWidget(SystemDialog)
        self.layoutWidget1.setGeometry(QtCore.QRect(36, 506, 184, 48))
        self.layoutWidget1.setObjectName("layoutWidget1")
        self.verticalLayout_2 = QtWidgets.QVBoxLayout(self.layoutWidget1)
        self.verticalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.verticalLayout_2.setSpacing(6)
        self.verticalLayout_2.setObjectName("verticalLayout_2")
        self.cb_save_geometry = LabeledToggle(self.layoutWidget1)
        self.cb_save_geometry.setObjectName("cb_save_geometry")
        self.verticalLayout_2.addWidget(self.cb_save_geometry)
        self.cb_save_view = LabeledToggle(self.layoutWidget1)
        self.cb_save_view.setObjectName("cb_save_view")
        self.verticalLayout_2.addWidget(self.cb_save_view)
        self.label_10 = QtWidgets.QLabel(SystemDialog)
        self.label_10.setGeometry(QtCore.QRect(315, 152, 151, 16))
        self.label_10.setObjectName("label_10")
        self.line_3 = QtWidgets.QFrame(SystemDialog)
        self.line_3.setGeometry(QtCore.QRect(283, 10, 20, 141))
        font = QtGui.QFont()
        font.setBold(False)
        font.setWeight(50)
        self.line_3.setFont(font)
        self.line_3.setLineWidth(2)
        self.line_3.setFrameShape(QtWidgets.QFrame.VLine)
        self.line_3.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.line_3.setObjectName("line_3")
        self.layoutWidget2 = QtWidgets.QWidget(SystemDialog)
        self.layoutWidget2.setGeometry(QtCore.QRect(310, 10, 307, 22))
        self.layoutWidget2.setObjectName("layoutWidget2")
        self.horizontalLayout_3 = QtWidgets.QHBoxLayout(self.layoutWidget2)
        self.horizontalLayout_3.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout_3.setObjectName("horizontalLayout_3")
        self.labelLaunch = QtWidgets.QLabel(self.layoutWidget2)
        font = QtGui.QFont()
        font.setBold(True)
        font.setUnderline(True)
        font.setWeight(75)
        self.labelLaunch.setFont(font)
        self.labelLaunch.setObjectName("labelLaunch")
        self.horizontalLayout_3.addWidget(self.labelLaunch)
        self.cb_al_enable = LabeledToggle(self.layoutWidget2)
        font = QtGui.QFont()
        font.setBold(False)
        font.setUnderline(False)
        font.setWeight(50)
        self.cb_al_enable.setFont(font)
        self.cb_al_enable.setObjectName("cb_al_enable")
        self.horizontalLayout_3.addWidget(self.cb_al_enable)
        self.label_13 = QtWidgets.QLabel(SystemDialog)
        self.label_13.setGeometry(QtCore.QRect(210, 484, 191, 20))
        self.label_13.setObjectName("label_13")
        self.layoutWidget3 = QtWidgets.QWidget(SystemDialog)
        self.layoutWidget3.setGeometry(QtCore.QRect(222, 506, 371, 80))
        self.layoutWidget3.setObjectName("layoutWidget3")
        self.gridLayout_4 = QtWidgets.QGridLayout(self.layoutWidget3)
        self.gridLayout_4.setContentsMargins(0, 0, 0, 0)
        self.gridLayout_4.setVerticalSpacing(6)
        self.gridLayout_4.setObjectName("gridLayout_4")
        self.browseVPConfStartup = QtWidgets.QToolButton(self.layoutWidget3)
        self.browseVPConfStartup.setEnabled(False)
        self.browseVPConfStartup.setObjectName("browseVPConfStartup")
        self.gridLayout_4.addWidget(self.browseVPConfStartup, 0, 2, 1, 1)
        self.enableVPConfStartup = LabeledToggle(self.layoutWidget3)
        self.enableVPConfStartup.setObjectName("enableVPConfStartup")
        self.gridLayout_4.addWidget(self.enableVPConfStartup, 0, 0, 1, 1)
        self.browseVPConfExit = QtWidgets.QToolButton(self.layoutWidget3)
        self.browseVPConfExit.setEnabled(False)
        self.browseVPConfExit.setObjectName("browseVPConfExit")
        self.gridLayout_4.addWidget(self.browseVPConfExit, 2, 2, 1, 1, QtCore.Qt.AlignRight)
        self.pathVPConfExit = QtWidgets.QLineEdit(self.layoutWidget3)
        self.pathVPConfExit.setEnabled(False)
        self.pathVPConfExit.setObjectName("pathVPConfExit")
        self.gridLayout_4.addWidget(self.pathVPConfExit, 2, 1, 1, 1)
        self.pathVPConfStartup = QtWidgets.QLineEdit(self.layoutWidget3)
        self.pathVPConfStartup.setEnabled(False)
        self.pathVPConfStartup.setObjectName("pathVPConfStartup")
        self.gridLayout_4.addWidget(self.pathVPConfStartup, 0, 1, 1, 1)
        self.enableVPConfExit = LabeledToggle(self.layoutWidget3)
        self.enableVPConfExit.setObjectName("enableVPConfExit")
        self.gridLayout_4.addWidget(self.enableVPConfExit, 2, 0, 1, 1, QtCore.Qt.AlignLeft)
        self.horizontalLayout_4 = QtWidgets.QHBoxLayout()
        self.horizontalLayout_4.setObjectName("horizontalLayout_4")
        spacerItem2 = QtWidgets.QSpacerItem(20, 20, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
        self.horizontalLayout_4.addItem(spacerItem2)
        self.enableVPConfGlobalDefault = LabeledToggle(self.layoutWidget3)
        self.enableVPConfGlobalDefault.setEnabled(False)
        self.enableVPConfGlobalDefault.setObjectName("enableVPConfGlobalDefault")
        self.horizontalLayout_4.addWidget(self.enableVPConfGlobalDefault)
        self.gridLayout_4.addLayout(self.horizontalLayout_4, 1, 0, 1, 3)
        self.buttonChildSettings = QtWidgets.QPushButton(SystemDialog)
        self.buttonChildSettings.setGeometry(QtCore.QRect(21, 576, 171, 23))
        self.buttonChildSettings.setObjectName("buttonChildSettings")
        self.layoutWidget4 = QtWidgets.QWidget(SystemDialog)
        self.layoutWidget4.setGeometry(QtCore.QRect(20, 206, 544, 244))
        self.layoutWidget4.setObjectName("layoutWidget4")
        self.gridLayout_3 = QtWidgets.QGridLayout(self.layoutWidget4)
        self.gridLayout_3.setContentsMargins(0, 0, 0, 0)
        self.gridLayout_3.setVerticalSpacing(6)
        self.gridLayout_3.setObjectName("gridLayout_3")
        self.enableDCS = LabeledToggle(self.layoutWidget4)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.enableDCS.sizePolicy().hasHeightForWidth())
        self.enableDCS.setSizePolicy(sizePolicy)
        self.enableDCS.setObjectName("enableDCS")
        self.gridLayout_3.addWidget(self.enableDCS, 0, 0, 1, 1)
        self.enableMSFS = LabeledToggle(self.layoutWidget4)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.enableMSFS.sizePolicy().hasHeightForWidth())
        self.enableMSFS.setSizePolicy(sizePolicy)
        self.enableMSFS.setObjectName("enableMSFS")
        self.gridLayout_3.addWidget(self.enableMSFS, 1, 0, 1, 1)
        self.enableXPLANE = LabeledToggle(self.layoutWidget4)
        self.enableXPLANE.setObjectName("enableXPLANE")
        self.gridLayout_3.addWidget(self.enableXPLANE, 2, 0, 1, 1)
        self.horizontalLayout = QtWidgets.QHBoxLayout()
        self.horizontalLayout.setObjectName("horizontalLayout")
        spacerItem3 = QtWidgets.QSpacerItem(13, 20, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
        self.horizontalLayout.addItem(spacerItem3)
        self.validateXPLANE = LabeledToggle(self.layoutWidget4)
        self.validateXPLANE.setObjectName("validateXPLANE")
        self.horizontalLayout.addWidget(self.validateXPLANE)
        self.gridLayout_3.addLayout(self.horizontalLayout, 3, 0, 1, 1)
        self.horizontalLayout_2 = QtWidgets.QHBoxLayout()
        self.horizontalLayout_2.setObjectName("horizontalLayout_2")
        spacerItem4 = QtWidgets.QSpacerItem(13, 20, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
        self.horizontalLayout_2.addItem(spacerItem4)
        self.lab_pathXPLANE = QtWidgets.QLabel(self.layoutWidget4)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.lab_pathXPLANE.sizePolicy().hasHeightForWidth())
        self.lab_pathXPLANE.setSizePolicy(sizePolicy)
        self.lab_pathXPLANE.setMinimumSize(QtCore.QSize(98, 0))
        self.lab_pathXPLANE.setObjectName("lab_pathXPLANE")
        self.horizontalLayout_2.addWidget(self.lab_pathXPLANE)
        self.pathXPLANE = QtWidgets.QLineEdit(self.layoutWidget4)
        self.pathXPLANE.setObjectName("pathXPLANE")
        self.horizontalLayout_2.addWidget(self.pathXPLANE)
        self.browseXPLANE = QtWidgets.QToolButton(self.layoutWidget4)
        self.browseXPLANE.setObjectName("browseXPLANE")
        self.horizontalLayout_2.addWidget(self.browseXPLANE)
        self.gridLayout_3.addLayout(self.horizontalLayout_2, 4, 0, 1, 1)
        self.enableIL2 = LabeledToggle(self.layoutWidget4)
        self.enableIL2.setObjectName("enableIL2")
        self.gridLayout_3.addWidget(self.enableIL2, 5, 0, 1, 1)
        self.horizontalLayout_6 = QtWidgets.QHBoxLayout()
        self.horizontalLayout_6.setObjectName("horizontalLayout_6")
        spacerItem5 = QtWidgets.QSpacerItem(13, 20, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
        self.horizontalLayout_6.addItem(spacerItem5)
        self.validateIL2 = LabeledToggle(self.layoutWidget4)
        self.validateIL2.setObjectName("validateIL2")
        self.horizontalLayout_6.addWidget(self.validateIL2)
        self.gridLayout_3.addLayout(self.horizontalLayout_6, 6, 0, 1, 1)
        self.horizontalLayout_7 = QtWidgets.QHBoxLayout()
        self.horizontalLayout_7.setObjectName("horizontalLayout_7")
        spacerItem6 = QtWidgets.QSpacerItem(13, 20, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
        self.horizontalLayout_7.addItem(spacerItem6)
        self.lab_pathIL2 = QtWidgets.QLabel(self.layoutWidget4)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.lab_pathIL2.sizePolicy().hasHeightForWidth())
        self.lab_pathIL2.setSizePolicy(sizePolicy)
        self.lab_pathIL2.setMinimumSize(QtCore.QSize(98, 0))
        self.lab_pathIL2.setObjectName("lab_pathIL2")
        self.horizontalLayout_7.addWidget(self.lab_pathIL2)
        self.pathIL2 = QtWidgets.QLineEdit(self.layoutWidget4)
        self.pathIL2.setObjectName("pathIL2")
        self.horizontalLayout_7.addWidget(self.pathIL2)
        self.browseIL2 = QtWidgets.QToolButton(self.layoutWidget4)
        self.browseIL2.setObjectName("browseIL2")
        self.horizontalLayout_7.addWidget(self.browseIL2)
        self.gridLayout_3.addLayout(self.horizontalLayout_7, 7, 0, 1, 1)
        self.horizontalLayout_8 = QtWidgets.QHBoxLayout()
        self.horizontalLayout_8.setObjectName("horizontalLayout_8")
        spacerItem7 = QtWidgets.QSpacerItem(13, 20, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
        self.horizontalLayout_8.addItem(spacerItem7)
        self.lab_portIL2 = QtWidgets.QLabel(self.layoutWidget4)
        self.lab_portIL2.setObjectName("lab_portIL2")
        self.horizontalLayout_8.addWidget(self.lab_portIL2)
        self.portIL2 = QtWidgets.QLineEdit(self.layoutWidget4)
        self.portIL2.setObjectName("portIL2")
        self.horizontalLayout_8.addWidget(self.portIL2)
        spacerItem8 = QtWidgets.QSpacerItem(279, 20, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Minimum)
        self.horizontalLayout_8.addItem(spacerItem8)
        self.gridLayout_3.addLayout(self.horizontalLayout_8, 8, 0, 1, 1)
        self.widget = QtWidgets.QWidget(SystemDialog)
        self.widget.setGeometry(QtCore.QRect(313, 45, 278, 106))
        self.widget.setObjectName("widget")
        self.gridLayout_2 = QtWidgets.QGridLayout(self.widget)
        self.gridLayout_2.setContentsMargins(0, 0, 0, 0)
        self.gridLayout_2.setObjectName("gridLayout_2")
        self.label_9 = QtWidgets.QLabel(self.widget)
        self.label_9.setObjectName("label_9")
        self.gridLayout_2.addWidget(self.label_9, 0, 0, 1, 1)
        self.label_12 = QtWidgets.QLabel(self.widget)
        self.label_12.setAlignment(QtCore.Qt.AlignLeading|QtCore.Qt.AlignLeft|QtCore.Qt.AlignTop)
        self.label_12.setObjectName("label_12")
        self.gridLayout_2.addWidget(self.label_12, 0, 1, 1, 1)
        self.lab_auto_launch = QtWidgets.QLabel(self.widget)
        self.lab_auto_launch.setEnabled(False)
        self.lab_auto_launch.setAlignment(QtCore.Qt.AlignLeading|QtCore.Qt.AlignLeft|QtCore.Qt.AlignTop)
        self.lab_auto_launch.setObjectName("lab_auto_launch")
        self.gridLayout_2.addWidget(self.lab_auto_launch, 0, 2, 1, 1)
        self.lab_start_min = QtWidgets.QLabel(self.widget)
        self.lab_start_min.setEnabled(False)
        self.lab_start_min.setAlignment(QtCore.Qt.AlignLeading|QtCore.Qt.AlignLeft|QtCore.Qt.AlignTop)
        self.lab_start_min.setObjectName("lab_start_min")
        self.gridLayout_2.addWidget(self.lab_start_min, 0, 3, 1, 1)
        self.lab_start_headless = QtWidgets.QLabel(self.widget)
        self.lab_start_headless.setEnabled(False)
        self.lab_start_headless.setAlignment(QtCore.Qt.AlignLeading|QtCore.Qt.AlignLeft|QtCore.Qt.AlignTop)
        self.lab_start_headless.setObjectName("lab_start_headless")
        self.gridLayout_2.addWidget(self.lab_start_headless, 0, 4, 1, 1)
        self.rb_master_j = QtWidgets.QRadioButton(self.widget)
        self.rb_master_j.setChecked(True)
        self.rb_master_j.setObjectName("rb_master_j")
        self.gridLayout_2.addWidget(self.rb_master_j, 1, 0, 1, 1)
        self.tb_pid_j = QtWidgets.QLineEdit(self.widget)
        self.tb_pid_j.setMaximumSize(QtCore.QSize(50, 16777215))
        self.tb_pid_j.setObjectName("tb_pid_j")
        self.gridLayout_2.addWidget(self.tb_pid_j, 1, 1, 1, 1)
        self.cb_al_enable_j = QtWidgets.QCheckBox(self.widget)
        self.cb_al_enable_j.setEnabled(False)
        self.cb_al_enable_j.setMaximumSize(QtCore.QSize(15, 16777215))
        self.cb_al_enable_j.setText("")
        self.cb_al_enable_j.setObjectName("cb_al_enable_j")
        self.gridLayout_2.addWidget(self.cb_al_enable_j, 1, 2, 1, 1, QtCore.Qt.AlignHCenter)
        self.cb_min_enable_j = QtWidgets.QCheckBox(self.widget)
        self.cb_min_enable_j.setEnabled(False)
        self.cb_min_enable_j.setMaximumSize(QtCore.QSize(15, 16777215))
        self.cb_min_enable_j.setText("")
        self.cb_min_enable_j.setCheckable(True)
        self.cb_min_enable_j.setObjectName("cb_min_enable_j")
        self.gridLayout_2.addWidget(self.cb_min_enable_j, 1, 3, 1, 1, QtCore.Qt.AlignHCenter)
        self.cb_headless_j = QtWidgets.QCheckBox(self.widget)
        self.cb_headless_j.setEnabled(False)
        self.cb_headless_j.setMaximumSize(QtCore.QSize(15, 16777215))
        self.cb_headless_j.setText("")
        self.cb_headless_j.setCheckable(True)
        self.cb_headless_j.setObjectName("cb_headless_j")
        self.gridLayout_2.addWidget(self.cb_headless_j, 1, 4, 1, 1, QtCore.Qt.AlignHCenter)
        self.rb_master_p = QtWidgets.QRadioButton(self.widget)
        self.rb_master_p.setObjectName("rb_master_p")
        self.gridLayout_2.addWidget(self.rb_master_p, 2, 0, 1, 1)
        self.tb_pid_p = QtWidgets.QLineEdit(self.widget)
        self.tb_pid_p.setMaximumSize(QtCore.QSize(50, 16777215))
        self.tb_pid_p.setObjectName("tb_pid_p")
        self.gridLayout_2.addWidget(self.tb_pid_p, 2, 1, 1, 1)
        self.cb_al_enable_p = QtWidgets.QCheckBox(self.widget)
        self.cb_al_enable_p.setEnabled(False)
        self.cb_al_enable_p.setMaximumSize(QtCore.QSize(15, 16777215))
        self.cb_al_enable_p.setText("")
        self.cb_al_enable_p.setObjectName("cb_al_enable_p")
        self.gridLayout_2.addWidget(self.cb_al_enable_p, 2, 2, 1, 1, QtCore.Qt.AlignHCenter)
        self.cb_min_enable_p = QtWidgets.QCheckBox(self.widget)
        self.cb_min_enable_p.setEnabled(False)
        self.cb_min_enable_p.setMaximumSize(QtCore.QSize(15, 16777215))
        self.cb_min_enable_p.setText("")
        self.cb_min_enable_p.setCheckable(True)
        self.cb_min_enable_p.setObjectName("cb_min_enable_p")
        self.gridLayout_2.addWidget(self.cb_min_enable_p, 2, 3, 1, 1, QtCore.Qt.AlignHCenter)
        self.cb_headless_p = QtWidgets.QCheckBox(self.widget)
        self.cb_headless_p.setEnabled(False)
        self.cb_headless_p.setMaximumSize(QtCore.QSize(15, 16777215))
        self.cb_headless_p.setText("")
        self.cb_headless_p.setCheckable(True)
        self.cb_headless_p.setObjectName("cb_headless_p")
        self.gridLayout_2.addWidget(self.cb_headless_p, 2, 4, 1, 1, QtCore.Qt.AlignHCenter)
        self.rb_master_c = QtWidgets.QRadioButton(self.widget)
        self.rb_master_c.setObjectName("rb_master_c")
        self.gridLayout_2.addWidget(self.rb_master_c, 3, 0, 1, 1)
        self.tb_pid_c = QtWidgets.QLineEdit(self.widget)
        self.tb_pid_c.setMaximumSize(QtCore.QSize(50, 16777215))
        self.tb_pid_c.setObjectName("tb_pid_c")
        self.gridLayout_2.addWidget(self.tb_pid_c, 3, 1, 1, 1)
        self.cb_al_enable_c = QtWidgets.QCheckBox(self.widget)
        self.cb_al_enable_c.setEnabled(False)
        self.cb_al_enable_c.setMaximumSize(QtCore.QSize(15, 16777215))
        self.cb_al_enable_c.setText("")
        self.cb_al_enable_c.setObjectName("cb_al_enable_c")
        self.gridLayout_2.addWidget(self.cb_al_enable_c, 3, 2, 1, 1, QtCore.Qt.AlignHCenter)
        self.cb_min_enable_c = QtWidgets.QCheckBox(self.widget)
        self.cb_min_enable_c.setEnabled(False)
        self.cb_min_enable_c.setMaximumSize(QtCore.QSize(15, 16777215))
        self.cb_min_enable_c.setText("")
        self.cb_min_enable_c.setCheckable(True)
        self.cb_min_enable_c.setObjectName("cb_min_enable_c")
        self.gridLayout_2.addWidget(self.cb_min_enable_c, 3, 3, 1, 1, QtCore.Qt.AlignHCenter)
        self.cb_headless_c = QtWidgets.QCheckBox(self.widget)
        self.cb_headless_c.setEnabled(False)
        self.cb_headless_c.setMaximumSize(QtCore.QSize(15, 16777215))
        self.cb_headless_c.setText("")
        self.cb_headless_c.setCheckable(True)
        self.cb_headless_c.setObjectName("cb_headless_c")
        self.gridLayout_2.addWidget(self.cb_headless_c, 3, 4, 1, 1, QtCore.Qt.AlignHCenter)

        self.retranslateUi(SystemDialog)
        QtCore.QMetaObject.connectSlotsByName(SystemDialog)
        SystemDialog.setTabOrder(self.logLevel, self.telemTimeout)
        SystemDialog.setTabOrder(self.telemTimeout, self.ignoreUpdate)
        SystemDialog.setTabOrder(self.ignoreUpdate, self.cb_al_enable)
        SystemDialog.setTabOrder(self.cb_al_enable, self.rb_master_j)
        SystemDialog.setTabOrder(self.rb_master_j, self.rb_master_p)
        SystemDialog.setTabOrder(self.rb_master_p, self.rb_master_c)
        SystemDialog.setTabOrder(self.rb_master_c, self.tb_pid_j)
        SystemDialog.setTabOrder(self.tb_pid_j, self.tb_pid_p)
        SystemDialog.setTabOrder(self.tb_pid_p, self.tb_pid_c)
        SystemDialog.setTabOrder(self.tb_pid_c, self.cb_al_enable_j)
        SystemDialog.setTabOrder(self.cb_al_enable_j, self.cb_al_enable_p)
        SystemDialog.setTabOrder(self.cb_al_enable_p, self.cb_al_enable_c)
        SystemDialog.setTabOrder(self.cb_al_enable_c, self.cb_min_enable_j)
        SystemDialog.setTabOrder(self.cb_min_enable_j, self.cb_min_enable_p)
        SystemDialog.setTabOrder(self.cb_min_enable_p, self.cb_min_enable_c)
        SystemDialog.setTabOrder(self.cb_min_enable_c, self.cb_headless_j)
        SystemDialog.setTabOrder(self.cb_headless_j, self.cb_headless_p)
        SystemDialog.setTabOrder(self.cb_headless_p, self.cb_headless_c)
        SystemDialog.setTabOrder(self.cb_headless_c, self.enableDCS)
        SystemDialog.setTabOrder(self.enableDCS, self.enableMSFS)
        SystemDialog.setTabOrder(self.enableMSFS, self.enableXPLANE)
        SystemDialog.setTabOrder(self.enableXPLANE, self.validateXPLANE)
        SystemDialog.setTabOrder(self.validateXPLANE, self.pathXPLANE)
        SystemDialog.setTabOrder(self.pathXPLANE, self.browseXPLANE)
        SystemDialog.setTabOrder(self.browseXPLANE, self.enableIL2)
        SystemDialog.setTabOrder(self.enableIL2, self.validateIL2)
        SystemDialog.setTabOrder(self.validateIL2, self.pathIL2)
        SystemDialog.setTabOrder(self.pathIL2, self.browseIL2)
        SystemDialog.setTabOrder(self.browseIL2, self.portIL2)
        SystemDialog.setTabOrder(self.portIL2, self.cb_save_geometry)
        SystemDialog.setTabOrder(self.cb_save_geometry, self.cb_save_view)
        SystemDialog.setTabOrder(self.cb_save_view, self.resetButton)

    def retranslateUi(self, SystemDialog):
        _translate = QtCore.QCoreApplication.translate
        SystemDialog.setWindowTitle(_translate("SystemDialog", "System Settings"))
        self.resetButton.setText(_translate("SystemDialog", "Reset to  Defaults"))
        self.label.setText(_translate("SystemDialog", "System Logging Level:"))
        self.lab_logPrune.setText(_translate("SystemDialog", "After:"))
        self.combo_logPrune.setItemText(0, _translate("SystemDialog", "Day(s)"))
        self.combo_logPrune.setItemText(1, _translate("SystemDialog", "Week(s)"))
        self.combo_logPrune.setItemText(2, _translate("SystemDialog", "Month(s)"))
        self.cb_logPrune.setText(_translate("SystemDialog", "Prune Logs (Global):"))
        self.label_2.setText(_translate("SystemDialog", "Telemetry Timeout (ms):"))
        self.ignoreUpdate.setText(_translate("SystemDialog", "Disable Update Prompt on Startup"))
        self.labelSystem.setText(_translate("SystemDialog", "System:"))
        self.labelSim.setText(_translate("SystemDialog", "Sim Setup:"))
        self.labelOther.setText(_translate("SystemDialog", "Other Settings: "))
        self.label_8.setText(_translate("SystemDialog", "Startup  Behavior:"))
        self.cb_save_geometry.setText(_translate("SystemDialog", "Restore window position"))
        self.cb_save_view.setText(_translate("SystemDialog", "Restore last tab view"))
        self.label_10.setText(_translate("SystemDialog", "Rhino default = 2055"))
        self.labelLaunch.setText(_translate("SystemDialog", "Launch Options:"))
        self.cb_al_enable.setText(_translate("SystemDialog", "Enable Auto-Launch"))
        self.label_13.setText(_translate("SystemDialog", "VPforce Configurator Profiles:"))
        self.browseVPConfStartup.setText(_translate("SystemDialog", "..."))
        self.enableVPConfStartup.setText(_translate("SystemDialog", "Load on Startup:"))
        self.browseVPConfExit.setText(_translate("SystemDialog", "..."))
        self.enableVPConfExit.setText(_translate("SystemDialog", "Load on Exit:"))
        self.enableVPConfGlobalDefault.setToolTip(_translate("SystemDialog", "<html><head/><body><p>Startup profile will also re-load for any aircraft that does not</p><p>have an override defined at the Sim/Class/Model level.  <br/><br/>The profile will only re-load if the previously loaded aircraft changed</p><p>the profile and the new aircraft does not have one defined at the</p><p>Sim/Class/Model level.</p></body></html>"))
        self.enableVPConfGlobalDefault.setText(_translate("SystemDialog", "Make Startup Profile Global Default"))
        self.buttonChildSettings.setText(_translate("SystemDialog", "Open Child Instance Settings"))
        self.enableDCS.setText(_translate("SystemDialog", "Enable DCS World Support"))
        self.enableMSFS.setText(_translate("SystemDialog", "Enable MSFS 2020 Support"))
        self.enableXPLANE.setText(_translate("SystemDialog", "Enable X-Plane Support"))
        self.validateXPLANE.setText(_translate("SystemDialog", "Auto X-Plane setup"))
        self.lab_pathXPLANE.setText(_translate("SystemDialog", "X-Plane Install Path:"))
        self.browseXPLANE.setText(_translate("SystemDialog", "..."))
        self.enableIL2.setText(_translate("SystemDialog", "Enable IL-2 Sturmovik Support"))
        self.validateIL2.setText(_translate("SystemDialog", "Auto IL-2 Telemetry setup"))
        self.lab_pathIL2.setText(_translate("SystemDialog", "IL-2 Install Path:"))
        self.browseIL2.setText(_translate("SystemDialog", "..."))
        self.lab_portIL2.setText(_translate("SystemDialog", "IL-2 Telemetry Port:"))
        self.label_9.setText(_translate("SystemDialog", "Master\n"
"Instance"))
        self.label_12.setText(_translate("SystemDialog", "USB\n"
"Product ID"))
        self.lab_auto_launch.setText(_translate("SystemDialog", "Auto\n"
"Launch:"))
        self.lab_start_min.setText(_translate("SystemDialog", "Start\n"
"Minimized:"))
        self.lab_start_headless.setText(_translate("SystemDialog", "Start\n"
"Headless:"))
        self.rb_master_j.setText(_translate("SystemDialog", "Joystick"))
        self.tb_pid_j.setText(_translate("SystemDialog", "2055"))
        self.rb_master_p.setText(_translate("SystemDialog", "Pedals"))
        self.rb_master_c.setText(_translate("SystemDialog", "Colective"))
from telemffb.custom_widgets import LabeledToggle
