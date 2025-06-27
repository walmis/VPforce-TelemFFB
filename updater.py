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


import json
import logging
import shutil
import ssl
import tempfile
import urllib.request
import traceback

import os
from zipfile import ZipFile
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QProgressBar, QPushButton, QLabel, QWidget, QHBoxLayout, QCheckBox, QMessageBox
import argparse
import subprocess
import sys

parser = argparse.ArgumentParser(description='TelemFFB Updater App')
parser.add_argument('--url', help='TelemFFB Zip File URL', default=None)
parser.add_argument('--debugzip', help='debug argument', default=None)
parser.add_argument('--debugpath', help='debug argument', default=None)
parser.add_argument('--current_version', help='Version of TelemFFB currently installed', default='dummy_text')

args, args_from_telemffb = parser.parse_known_args()
if args_from_telemffb != []:
    print(f'TelemFFB was started with args {args_from_telemffb} and passed them to updater')
# args.debugzip = r"C:\Users\micah\PycharmProjects\SettingsMgr\dist\VPforce-TelemFFB\TFFB.zip"

test = False
g_latest_version = None
g_latest_url = None
g_current_version = args.current_version
g_url_is_good = False
g_application_path = None
g_folder_contents = []
g_executable_name = 'VPforce-TelemFFB.exe'
if getattr(sys, 'frozen', False):
    g_application_path = os.path.dirname(sys.executable)
elif __file__:
    g_application_path = os.path.dirname(__file__)

if args.debugpath is not None:
    print(f"DEBUG: g_application_path = {args.debug}")
    g_application_path = args.debugpath
if args.debugzip is not None:
    print(f"DEBUG: args.debugzip = {args.debugzip}")


g_folder_contents = os.listdir(g_application_path)
def fetch_latest_version():
    global g_url_is_good

    current_version = args.current_version
    latest_version = None
    latest_url = None
    url = "https://vpforcecontrols.com/downloads/TelemFFB/"
    file = "latest.json"
    send_url = url + file

    try:
        with urllib.request.urlopen(send_url, context=ssl._create_unverified_context()) as req:
            latest = json.loads(req.read().decode())
            latest_version = latest["version"]
            latest_url = url + latest["filename"]
    except Exception as e:
        logging.exception(f"Error checking latest version status: {url}\n{e}")
        g_url_is_good = e

    if current_version != latest_version and latest_version is not None and latest_url is not None:
        logging.debug(f"Current version: {current_version} | Latest version: {latest_version}")
        g_url_is_good = True
        return latest_version, latest_url
    elif current_version == latest_version:
        g_url_is_good = False
        return False
    else:
        return None


class Downloader(QThread):
    update_progress = pyqtSignal(int)
    update_status_label = pyqtSignal(str)
    download_complete = pyqtSignal()
    extract_complete = pyqtSignal()

    def __init__(self, url, destination):
        super().__init__()
        self.url = url
        self.destination = destination

    def download(self):
        if args.debugzip is None:
            try:
                resp = urllib.request.urlopen(self.url, context=ssl._create_unverified_context())
                total_size = int(resp.headers.get('Content-Length'))
            except:
                QMessageBox.critical(None, "Error downloading latest version info",
                                     f"There was an error downloading the latest version:\n\nThe updater will now exit")
                sys.exit(-1)

            current_size = 0
            self.update_status_label.emit("Downloading Update:")

            zip_path = os.path.join(self.destination, "temp.zip")

            with open(zip_path, "wb") as zip_file:
                chunk = resp.read(1024)
                while chunk:
                    print(f"Downloading {zip_path}......")
                    current_size += len(chunk)
                    progress_percentage = int((current_size / total_size) * 100)
                    self.update_progress.emit(progress_percentage)
                    zip_file.write(chunk)

                    chunk = resp.read(1024)
        else:
            zip_path = args.debugzip
        self.update_progress.emit(100)
        print(f"~~~~ Download Completed ~~~~")
        self.update_status_label.emit("Download Completed:")
        self.download_complete.emit()
        self.extract(zip_path)

    def move_to_backup(self, file_list):

        source_folder = g_application_path
        backup_folder = os.path.join(source_folder, '_previous_version_backup')

        if os.path.exists(backup_folder):
            shutil.rmtree(backup_folder)
        print("Creating Backup of previous version files")
        # Create the backup directory if it doesn't exist
        os.makedirs(backup_folder, exist_ok=True)

        for f in file_list:
            if f in ['updater.exe', '_previous_version_backup']:
                continue
            elif f.endswith('.ini') and f != 'config.ini':
                shutil.copy(os.path.join(source_folder, f), os.path.join(backup_folder, f))
            else:
                shutil.move(os.path.join(source_folder, f), os.path.join(backup_folder, f))



    def extract(self, zip_path):
        # Simulate extraction process
        self.update_status_label.emit("Backing up current version files:")
        self.sleep(1)
        self.move_to_backup(g_folder_contents)
        self.update_status_label.emit("Extracting Update Files:")
        self.sleep(1)

        # workaround so the unzip succeeds (base_library.zip is required)
        try: 
            os.makedirs("assets", exist_ok=True)
            shutil.copy("_previous_version_backup/assets/base_library.zip", "assets/base_library.zip")
        except: 
            traceback.print_exc()
        ###

        # Create a temporary directory inside the script's folder
        temp_dir = tempfile.mkdtemp(dir=g_application_path)
        print(f"Extracting downloaded zip file to temp directory {temp_dir}")
        try:
            # Extract the contents of the zip file to the temporary directory
            with ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            print(f"~~~~ Extraction Completed ~~~~")
            # Copy and overwrite files from the temporary directory to the root directory
            for item in os.listdir(temp_dir):
                src = os.path.join(temp_dir, item)
                dest = os.path.join(self.destination, item)

                # # Rename a specific file during the copy process
                # if item == 'old_name.txt':
                #     dest = os.path.join(self.destination, 'new_name.txt')
                print(f"Copying new files.....")
                self.update_status_label.emit("Copying Files:")
                if os.path.isdir(src):
                    shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dest, follow_symlinks=False)
            self.sleep(1)

        except Exception as e:
            print(f"Error during extraction: {e}")
            traceback.print_exc()
        finally:
            # Clean up: remove the temporary directory
            print("Cleaning up temporary files")
            self.update_status_label.emit("Cleaning up temporary files:")
            self.sleep(1)
            shutil.rmtree(temp_dir)

        if args.debugzip is None:
            os.remove(zip_path)
        self.update_status_label.emit("Update Completed!")
        print(f"~~~~~ Update Completed ! ~~~~")
        self.sleep(1)
        self.extract_complete.emit()

    def sleep(self, seconds):
        # A simple sleep method for simulation
        import time
        time.sleep(seconds)

    def run(self):
        self.download()


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        global g_url_is_good
        if g_url_is_good is not True:
            QMessageBox.critical(None, "Error fetching latest version info", f"There was an error retrieving the latest version information:\n{g_url_is_good}\n\nThe updater will now exit")
            sys.exit(-1)
        self.setWindowTitle("VPforce TelemFFB Updater")
        self.setGeometry(100, 100, 400, 250)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Row layout for version labels and image
        version_layout = QHBoxLayout()

        self.version_label = QLabel(f"Current Installed Version: {g_current_version}\n\nLatest Available Version: {g_latest_version}")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        version_layout.addWidget(self.version_label)
        icon_path = os.path.join(g_application_path, "image/vpforceicon.png")
        self.setWindowIcon(QIcon(icon_path))
        # Add QLabel for the image
        image_label = QLabel(self)
        pixmap = QPixmap(icon_path)  # Replace with the path to your image
        image_label.setPixmap(pixmap)
        image_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
        version_layout.addWidget(image_label)

        # Add the row layout to the main layout
        layout.addLayout(version_layout)

        self.progress_bar = QProgressBar()
        self.progress_label = QLabel("Ready to Download")

        row_layout = QHBoxLayout()
        row_layout.addWidget(self.progress_label)
        row_layout.addWidget(self.progress_bar)

        layout.addStretch(1)
        layout.addLayout(row_layout)
        layout.addStretch(1)

        # Checkboxes
        self.launch_checkbox = QCheckBox("Launch TelemFFB When Complete")
        self.launch_checkbox.setChecked(True)

        self.release_notes_checkbox = QCheckBox("Read Release Notes")
        self.release_notes_checkbox.setChecked(False)

        checkbox_layout = QVBoxLayout()
        checkbox_layout.addWidget(self.launch_checkbox)
        checkbox_layout.addWidget(self.release_notes_checkbox)
        checkbox_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        layout.addLayout(checkbox_layout)
        layout.addStretch(1)

        self.update_button = QPushButton("Update")
        self.update_button.clicked.connect(self.start_download)

        self.finish_button = QPushButton("Finish")
        self.finish_button.clicked.connect(self.finish_update)
        self.finish_button.setVisible(False)

        self.exit_button = QPushButton("Exit")
        self.exit_button.clicked.connect(self.close)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.exit_button)
        button_layout.addWidget(self.update_button)
        button_layout.addWidget(self.finish_button)

        layout.addLayout(button_layout)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

    def start_download(self):
        global g_latest_version, g_latest_url, g_application_path

        self.downloader = Downloader(args.url, g_application_path)
        self.downloader.update_progress.connect(self.update_progress)
        self.downloader.update_status_label.connect(self.update_status_label)
        self.downloader.download_complete.connect(self.download_completed)
        self.downloader.extract_complete.connect(self.extract_completed)
        self.update_button.setDisabled(True)
        self.exit_button.setDisabled(True)
        self.downloader.start()

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def update_status_label(self, status):
        self.progress_label.setText(status)

    def download_completed(self):
        # Download is complete, make the Finish button visible
        pass

    def extract_completed(self):
        # Download is complete, make the Finish button visible
        self.finish_button.setVisible(True)
        self.finish_button.setDisabled(False)
        self.exit_button.setDisabled(False)
        self.update_button.setVisible(False)

        # Make the checkboxes visible
        self.launch_checkbox.setVisible(True)
        self.release_notes_checkbox.setVisible(True)

        # If "Launch on Exit" is checked, launch cmd.exe (replace with actual executable path)
        if self.launch_checkbox.isChecked():
            # Specify the path to the executable

            full = os.path.join(g_application_path, g_executable_name)
            full = [full] + args_from_telemffb
            # print(f"FULLL:{full}")
            # Use subprocess.Popen with the script's directory as the working directory
            subprocess.Popen(full, cwd=g_application_path, shell=False)
            if self.release_notes_checkbox.isChecked():
                rn_path = os.path.join(g_application_path, '_RELEASE_NOTES.txt')
                self.update_status_label("Re-Launching TelemFFB.....")
                rc = subprocess.Popen(['start', 'notepad.exe', rn_path], cwd=g_application_path, shell=True)
                rc.wait()
            sys.exit()

    def finish_update(self):
        # Show a message box if "Read Release Notes" is checked
        if self.launch_checkbox.isChecked():
            # Specify the path to the executable

            full = os.path.join(g_application_path, g_executable_name)
            # print(f"FULLL:{full}")
            # Use subprocess.Popen with the script's directory as the working directory
            subprocess.Popen([full], cwd=g_application_path, shell=False)

        if self.release_notes_checkbox.isChecked():
            rn_path = os.path.join(g_application_path, '_RELEASE_NOTES.txt')
            subprocess.Popen(['start', 'notepad.exe', rn_path], cwd=g_application_path, shell=True)

        # Close the updater app
        self.close()


def check_runtime():
    if args.current_version == 'dummy_text' and getattr(sys, 'frozen', False):
        QMessageBox.critical(None, "ERROR", "This application is not intended to be run in a standalone fashion.\n\nIf an update is available, you will be prompted to update upon starting TelemFFB")
        sys.exit()


def main():
    frozen = getattr(sys, 'frozen', False)
    if frozen:
        # we are running in a bundle
        bundle_dir = sys._MEIPASS
    else:
        # we are running in a normal Python environment
        bundle_dir = os.path.dirname(os.path.abspath(__file__))
    log_info = f'Frozen is {frozen}\nbundle dir is {bundle_dir}\nsys.argv[0] is {sys.argv[0]}\nsys.executable is {sys.executable}\nos.getcwd is {os.getcwd()}'

    print(log_info)
    global g_latest_version
    global g_latest_url
    try:
        g_latest_version, g_latest_url = fetch_latest_version()
    except:
        pass

    if args.url is None:
        args.url = g_latest_url

    app = QApplication([])
    window = App()
    window.show()
    # print(g_latest_version, g_latest_url)
    # print(g_folder_contents)
    # print(f"debugpath={args.debugpath}")

    check_runtime()
    app.exec()


if __name__ == "__main__":

    main()
