from telemffb.custom_widgets import QMutex, QThread, pyqtSignal
from telemffb.settingsmanager import os, pyqtSignal


from PyQt5.QtCore import QMutex, QThread, pyqtSignal


import os
import time


class LogTailer(QThread):
    log_updated = pyqtSignal(str)

    def __init__(self, log_file_path, parent=None):
        super(LogTailer, self).__init__(parent)
        self.log_file_path = log_file_path
        self.pause_mutex = QMutex()
        self.paused = False

    def run(self):
        with open(self.log_file_path, 'r') as self.log_file:
            self.log_file.seek(0, os.SEEK_END)
            while True:
                self.pause_mutex.lock()
                while self.paused:
                    self.pause_mutex.unlock()
                    time.sleep(0.1)
                    self.pause_mutex.lock()

                where = self.log_file.tell()
                line = self.log_file.readline()
                if not line:
                    time.sleep(0.1)
                    self.log_file.seek(where)
                else:
                    self.log_updated.emit(line)

                self.pause_mutex.unlock()

    def pause(self):
        self.pause_mutex.lock()
        self.paused = True
        self.pause_mutex.unlock()

    def resume(self):
        self.pause_mutex.lock()
        self.paused = False
        self.pause_mutex.unlock()

    def is_paused(self):
        return self.paused

    def change_log_file(self, new_log_file_path):
        self.pause()  # Pause the tailing while changing the log file
        if self.log_file:
            self.log_file.close()  # Close the current file handle
        self.log_file_path = new_log_file_path
        self.log_file = open(self.log_file_path, 'r')  # Open the new log file
        self.log_file.seek(0, os.SEEK_END)
        self.resume()  # Resume tailing with the new log file