#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
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

import json
import logging
import os
import re
import select
import socket
import ssl
import sys
import tempfile
import traceback
import urllib.error
import urllib.request
import uuid
import zipfile

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog

import telemffb.globals as G
from .settings import read_all_system_settings


__all__ = [
    "create_ssl_context",
    "open_url",
    "fetch_json_url",
    "post_multipart_url",
    "classify_http_exception",
    "format_exception_stackprinter",
    "create_support_bundle_data",
    "report_exceptions",
    "create_support_bundle",
    "sock_readable",
    "FetchLatestVersion",
]


def create_ssl_context():
    return ssl._create_unverified_context()


def _decode_http_response(response):
    return response.read().decode("utf-8", errors="replace")


def open_url(url, data=None, headers=None, timeout=30, method=None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    return urllib.request.urlopen(request, context=create_ssl_context(), timeout=timeout)


def fetch_json_url(url, timeout=30):
    with open_url(url, timeout=timeout) as response:
        return json.loads(_decode_http_response(response))


def _encode_multipart_formdata(fields=None, files=None):
    boundary = f"----TelemFFBBoundary{uuid.uuid4().hex}"
    body = bytearray()

    for name, value in fields or []:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        if isinstance(value, bytes):
            body.extend(value)
        else:
            body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for name, filename, content, content_type in files or []:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8")
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(content)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def post_multipart_url(url, files, fields=None, timeout=30):
    data, content_type = _encode_multipart_formdata(fields=fields, files=files)
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(data)),
        "Accept": "application/json",
    }
    with open_url(url, data=data, headers=headers, timeout=timeout, method="POST") as response:
        return response.status, _decode_http_response(response)


def classify_http_exception(exc):
    if isinstance(exc, urllib.error.HTTPError):
        try:
            text = _decode_http_response(exc)
        except Exception:
            text = str(exc)
        return {"status_code": exc.code, "text": text}

    if isinstance(exc, (TimeoutError, socket.timeout)):
        return {"error": "timeout"}

    reason = getattr(exc, "reason", None)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return {"error": "timeout"}

    if isinstance(exc, urllib.error.URLError):
        return {"error": "connection"}

    return {"error": str(exc), "traceback": traceback.format_exc()}


def _create_support_bundle_zip(zip_file_path, userconfig_rootpath, exceptions=None, user_info=None):
    """Internal helper to create a support bundle zip file.

    Args:
        zip_file_path: Output path for the zip file
        userconfig_rootpath: Path to user config directory
        exceptions: Optional list of ExceptionRecord objects to include
        user_info: Optional dict from the report dialog
            ('discord_username', 'notes') — written as the FIRST archive
            entry so support can map the bundle to a user at a glance
    """
    from datetime import datetime
    import telemffb.utils.winpaths as winpaths
    from .integration import get_dcs_variant


    # Get the system settings
    sys_dict = read_all_system_settings()

    # Create the support zip file directly
    with zipfile.ZipFile(zip_file_path, 'w', compression=zipfile.ZIP_LZMA, compresslevel=9) as support_zip:
        # User-supplied report context first: bundle-to-user mapping and
        # the reporter's own words are the highest-value triage data.
        if user_info is not None:
            lines = [
                f"Report created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"Device instance: {getattr(G, 'device_type', 'unknown')}",
                f"Discord username: {user_info.get('discord_username') or '(not provided)'}",
                "",
                "Additional information from the user:",
                user_info.get('notes') or '(none)',
                "",
            ]
            support_zip.writestr("user_report.txt", "\n".join(lines))

        # Add userconfig_v2.xml
        userconfig_path = os.path.join(userconfig_rootpath, "userconfig_v2.xml")
        legacy_userconfig_path = os.path.join(userconfig_rootpath, "userconfig.xml")

        if os.path.exists(userconfig_path):
            support_zip.write(userconfig_path, "userconfig_v2.xml")

        if os.path.exists(legacy_userconfig_path):
            support_zip.write(userconfig_path, "userconfig.xml")

        history_path = os.path.join(userconfig_rootpath, "match_history.json")
        if os.path.exists(history_path):
            support_zip.write(history_path, "match_history.json")

        # Add log files
        log_folder = os.path.join(userconfig_rootpath, "log")
        if os.path.exists(log_folder):
            for folder_name, subfolders, filenames in os.walk(log_folder):
                for filename in filenames:
                    file_path = os.path.join(folder_name, filename)
                    arcname = os.path.relpath(file_path, userconfig_rootpath)
                    support_zip.write(file_path, arcname)
        
        # Add system settings
        cfg_content = "\n".join(f"{key}={value}" for key, value in sys_dict.items())
        support_zip.writestr("system_settings.cfg", cfg_content)
        
        # Add exception details if provided
        if exceptions:
            exc_content = []
            exc_content.append(f"Exception Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            exc_content.append(f"Total Exceptions: {len(exceptions)}\n")
            exc_content.append("=" * 80 + "\n\n")
            
            for i, exc in enumerate(exceptions, 1):
                exc_content.append(f"Exception {i}/{len(exceptions)}\n")
                exc_content.append("-" * 80 + "\n")
                exc_content.append(exc.format_full())
                exc_content.append("\n" + "=" * 80 + "\n\n")
            
            support_zip.writestr("exceptions.txt", "".join(exc_content))
        
        # Add DCS files if available
        try:
            saved_games = winpaths.get_path(winpaths.FOLDERID.SavedGames)
            if saved_games:
                dcs_variant = get_dcs_variant()
                dcs_folders = ['DCS', 'DCS.openbeta']
                if dcs_variant and f'DCS.{dcs_variant}' not in dcs_folders:
                    dcs_folders.append(f'DCS.{dcs_variant}')
                
                for dcs_folder in dcs_folders:
                    dcs_path = os.path.join(saved_games, dcs_folder)
                    if os.path.exists(dcs_path):
                        # Add DCS log file
                        dcs_log = os.path.join(dcs_path, "Logs", "dcs.log")
                        if os.path.exists(dcs_log):
                            support_zip.write(dcs_log, f"{dcs_folder}/Logs/dcs.log")
                        
                        # Add Export.lua if present
                        export_lua = os.path.join(dcs_path, "Scripts", "Export.lua")
                        if os.path.exists(export_lua):
                            support_zip.write(export_lua, f"{dcs_folder}/Scripts/Export.lua")
        except Exception as e:
            # If DCS detection fails, continue without DCS files
            pass


def format_exception_stackprinter(exc_info):
    """Format an exception with stackprinter for a debug-friendly traceback.

    The output includes source-code context and the values of local variables
    at each frame, which is far more useful for diagnosing field issues than
    the stock ``traceback`` output.

    ``exc_info`` may be a ``sys.exc_info()``-style 3-tuple
    ``(type, value, tb)`` or a single exception instance.

    This is always safe to call from logging / excepthook paths: if
    ``stackprinter`` is not installed, or fails (e.g. source files are
    unavailable in a frozen build), it falls back to the standard
    :mod:`traceback` formatting. ``style='plaintext'`` keeps the output
    ANSI-free, which the log pipeline (and the file handler) require.
    """
    # Normalize to a (type, value, tb) triple.
    if isinstance(exc_info, BaseException):
        etype, evalue, tb = type(exc_info), exc_info, exc_info.__traceback__
    elif isinstance(exc_info, tuple) and len(exc_info) == 3:
        etype, evalue, tb = exc_info
    else:
        return ""

    try:
        import stackprinter
        return stackprinter.format(
            (etype, evalue, tb),
            style='plaintext',
            show_vals='like_source',
            truncate_vals=500,
            source_lines=5,
        )
    except Exception:
        # Never let a formatting failure break logging; fall back to the
        # standard traceback.
        try:
            return "".join(traceback.format_exception(etype, evalue, tb))
        except Exception:
            return ""


def create_support_bundle_data(userconfig_rootpath, exceptions=None, user_info=None):
    """Create support bundle as bytes (in memory) for API upload.

    Args:
        userconfig_rootpath: Path to user config directory
        exceptions: Optional list of ExceptionRecord objects to include
        user_info: Optional dict from the report dialog (see
            _create_support_bundle_zip)

    Returns:
        bytes: Support bundle as zip file data
    """
    # Use a temporary file to create the zip, then read it into memory
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        _create_support_bundle_zip(tmp_path, userconfig_rootpath, exceptions, user_info=user_info)
        with open(tmp_path, 'rb') as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def report_exceptions(parent_widget=None, on_complete_callback=None):
    """Report exceptions by uploading support bundle to API.
    
    This function runs bundle creation and HTTP POST in a background QThread
    so the Qt event loop (UI) remains responsive. It includes user prompts
    and confirmation dialogs.
    
    Args:
        parent_widget: Parent QWidget for dialog boxes (can be None)
        on_complete_callback: Optional callback function to call when upload completes (success or failure)
    
    Returns:
        bool: True if upload process started, False if user cancelled or no exceptions
    """
    
    exceptions_list = G.exception_tracker.get_exceptions()

    # Confirmation dialog with optional reporter context. The Discord
    # username lets support map an uploaded bundle to the person asking
    # about it on the VPforce Discord. It is remembered for THIS SESSION
    # only (module attribute) — deliberately never persisted to the
    # registry/disk: it is personal data the user types for a support
    # interaction, not configuration. Both fields land in user_report.txt
    # at the top of the bundle.
    from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel,
                                 QLineEdit, QPlainTextEdit, QVBoxLayout)
    dlg = QDialog(parent_widget)
    dlg.setWindowTitle("Report Exceptions")
    dlg_layout = QVBoxLayout(dlg)
    dlg_layout.addWidget(QLabel(
        "Upload Support Bundle to VPforce support?\n\n"
        "**Note** - this is not a replacement for posting your issue/question\n"
        "in the VPforce discord.  Nobody is going to proactively reachout to you.\n"
        "It will simply help associate your discord message with this support bundle.\n\n"
        "This will include:\n"
        "  • Exception details and tracebacks\n"
        "  • System configuration\n"
        "  • Application logs"
    ))
    dlg_layout.addSpacing(8)
    dlg_layout.addWidget(QLabel(
        "Discord username (optional) — lets support match this bundle to "
        "you\non the VPforce Discord:"))
    tb_discord = QLineEdit()
    tb_discord.setPlaceholderText("your Discord username")
    tb_discord.setText(getattr(report_exceptions, '_session_discord_username', ''))
    dlg_layout.addWidget(tb_discord)
    dlg_layout.addWidget(QLabel(
        "Additional information (optional) — what were you doing when the\n"
        "problem occurred, or anything else support should know:"))
    tb_notes = QPlainTextEdit()
    tb_notes.setPlaceholderText("Describe what happened…")
    tb_notes.setMinimumHeight(90)
    dlg_layout.addWidget(tb_notes)
    dlg_layout.addWidget(QLabel(
        "You will need to complete a verification challenge."))
    dlg_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
    dlg_buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Upload")
    dlg_buttons.accepted.connect(dlg.accept)
    dlg_buttons.rejected.connect(dlg.reject)
    dlg_layout.addWidget(dlg_buttons)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False

    discord_username = tb_discord.text().strip()
    user_notes = tb_notes.toPlainText().strip()
    report_exceptions._session_discord_username = discord_username
    user_info = {'discord_username': discord_username, 'notes': user_notes}

    # Progress dialog (indeterminate)
    progress = QProgressDialog("Creating support bundle and uploading to server...", None, 0, 0, parent_widget)
    progress.setWindowTitle("Uploading...")
    progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.show()

    # Worker that runs in background thread
    class UploadWorker(QObject):
        finished = pyqtSignal(bool, dict)

        def __init__(self, api_url: str, userconfig_rootpath: str, exceptions_list, user_info=None):
            super().__init__()
            self.api_url = api_url
            self.userconfig_rootpath = userconfig_rootpath
            self.exceptions_list = exceptions_list
            self.user_info = user_info

        def run(self):
            try:
                # Create bundle in memory
                bundle = create_support_bundle_data(self.userconfig_rootpath, self.exceptions_list,
                                                    user_info=self.user_info)

                # Embed the (sanitized) Discord username in the uploaded
                # filename: if the support server passes the client filename
                # through to the Discord attachment, the bundle-to-user
                # mapping becomes visible right in the automated message —
                # no server change needed. Falls back to the plain name.
                filename = 'support_bundle.zip'
                uname = (self.user_info or {}).get('discord_username') or ''
                uname = re.sub(r'[^A-Za-z0-9_.-]', '', uname)[:48]
                if uname:
                    filename = f'support_bundle_{uname}.zip'

                status_code, response_text = post_multipart_url(
                    self.api_url,
                    files=[('bundle', filename, bundle, 'application/zip')],
                    timeout=30,
                )

                if status_code == 200:
                    try:
                        payload = json.loads(response_text) if response_text else {'challenge_url': None}
                    except Exception:
                        payload = {'challenge_url': None}
                    self.finished.emit(True, payload)
                else:
                    self.finished.emit(False, {'status_code': status_code, 'text': response_text})

            except Exception as e:
                self.finished.emit(False, classify_http_exception(e))

    # Build API URL and capture userconfig path now
    userconfig_rootpath = G.userconfig_rootpath
    api_url = 'https://vpforce.eu/telemffb/api/upload'

    # Prepare thread and worker
    thread = QThread(parent_widget)
    worker = UploadWorker(api_url, userconfig_rootpath, exceptions_list, user_info=user_info)
    worker.moveToThread(thread)

    # Connect signals
    thread.started.connect(worker.run)

    def on_finished(success: bool, payload: dict):
        try:
            progress.close()
        except Exception:
            pass

        if success:
            challenge_url = payload.get('challenge_url') if isinstance(payload, dict) else None
            if challenge_url:
                # Open challenge URL in browser from main thread
                import webbrowser
                webbrowser.open(challenge_url)
                QMessageBox.information(
                    parent_widget,
                    "Verification Required",
                    "A verification page has been opened in your browser.\n\nPlease complete the challenge to submit your report.",
                )
            else:
                QMessageBox.warning(parent_widget, "Upload Error", "Server did not return a challenge URL.")
        else:
            # Handle common errors
            if payload.get('error') == 'connection':
                QMessageBox.critical(
                    parent_widget,
                    "Connection Error",
                    "Could not connect to the support server.\n\nPlease check your internet connection and try again.",
                )
            elif payload.get('error') == 'timeout':
                QMessageBox.critical(parent_widget, "Timeout Error", "Upload timed out.\n\nPlease try again later.")
            else:
                # Show server response if available
                status = payload.get('status_code')
                text = payload.get('text') or payload.get('error') or ''
                if status:
                    QMessageBox.critical(
                        parent_widget,
                        "Upload Failed",
                        f"Failed to upload support bundle.\n\nStatus: {status}\nMessage: {text[:200]}",
                    )
                else:
                    QMessageBox.critical(
                        parent_widget,
                        "Upload Error",
                        f"An error occurred while uploading:\n\n{text}",
                    )

        # Clean up thread and worker
        try:
            thread.quit()
            thread.wait(2000)
        except Exception:
            pass

        worker.deleteLater()
        thread.deleteLater()
        
        # Call completion callback if provided
        if on_complete_callback:
            try:
                on_complete_callback(success, payload)
            except Exception:
                pass

    worker.finished.connect(on_finished)
    # Ensure we stop the thread if it finishes
    worker.finished.connect(thread.quit)
    thread.start()
    
    return True


def create_support_bundle(userconfig_rootpath):
    """Create a support bundle with a file save dialog, showing progress during creation.
    
    This function runs bundle creation in a background QThread with an indeterminate
    progress dialog, keeping the UI responsive during the zip operation.
    
    Args:
        userconfig_rootpath: Path to user config directory
    """
    # Prompt the user for the destination and filename for the zip file
    file_dialog = QFileDialog()
    file_dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    file_dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    file_dialog.setNameFilter("Zip Files (*.zip)")

    if not file_dialog.exec():
        return

    # Get the selected file path
    zip_file_path = file_dialog.selectedFiles()[0]

    # Progress dialog (indeterminate)
    progress = QProgressDialog("Creating support bundle...", None, 0, 0)
    progress.setWindowTitle("Creating Support Bundle")
    progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.show()

    # Worker to perform the zip creation in background
    class BundleWorker(QObject):
        finished = pyqtSignal(bool, dict)

        def __init__(self, userconfig_rootpath, zip_file_path):
            super().__init__()
            self.userconfig_rootpath = userconfig_rootpath
            self.zip_file_path = zip_file_path

        def run(self):
            try:
                _create_support_bundle_zip(self.zip_file_path, self.userconfig_rootpath)
                # Success
                self.finished.emit(True, {"path": self.zip_file_path})
            except Exception as e:
                # Report error
                try:
                    self.finished.emit(False, {"error": str(e)})
                except Exception:
                    pass

    # Prepare thread and worker
    thread = QThread()
    worker = BundleWorker(userconfig_rootpath, zip_file_path)
    worker.moveToThread(thread)

    # Connect signals
    thread.started.connect(worker.run)

    def _on_finished(success: bool, payload: dict):
        try:
            progress.close()
        except Exception:
            pass

        if success:
            try:
                QMessageBox.information(
                    None, 
                    "Support Bundle Created", 
                    f"Support bundle saved to:\n{payload.get('path')}"
                )
            except Exception:
                pass
        else:
            try:
                QMessageBox.critical(
                    None, 
                    "Error", 
                    f"Failed to create support bundle:\n{payload.get('error')}"
                )
            except Exception:
                pass

        # Cleanup
        try:
            thread.quit()
            thread.wait(2000)
        except Exception:
            pass

        worker.deleteLater()
        thread.deleteLater()

    worker.finished.connect(_on_finished)
    # Ensure thread stops when finished
    worker.finished.connect(thread.quit)
    thread.start()


def sock_readable(s) -> bool:
    r, _, _ = select.select([s], [], [], 0)
    return s in r


class FetchLatestVersion(QThread):
    workers = []

    version_result_signal = pyqtSignal(str, str)
    error_signal = pyqtSignal(str)
    def __init__(self, on_fetch, on_error) -> None:
        super().__init__()
        if on_fetch:
            self.version_result_signal.connect(on_fetch)
        if on_error:
            self.error_signal.connect(on_error)
        self.__class__.workers.append(self)
        self.start()


    def run(self):
        from .integration import get_version
        try:
            current_version = get_version()
            latest_version = None
            latest_url = None
            url = "https://vpforcecontrols.com/downloads/TelemFFB/"
            file = "latest.json"
            send_url = url + file

            if 'dirty' in current_version:
                logging.info("Running from source with locally modified files, skipping version check")
            else:
                try:
                    latest = fetch_json_url(send_url, timeout=10)
                    latest_version = latest["version"]
                    latest_url = url + latest["filename"]
                except Exception as e:
                    logging.exception(f"Error checking latest version status: {url}")
                    self.error_signal.emit(str(e))
            if getattr(sys, 'frozen', False):
                if 'local' in current_version or 'dirty' in current_version:
                    self.version_result_signal.emit('dev', 'dev')
                elif current_version != latest_version and latest_version is not None and latest_url is not None:
                    logging.debug(f"Current version: {current_version} | Latest version: {latest_version}")
                    self.version_result_signal.emit(latest_version, latest_url)
                elif current_version == latest_version:
                    self.version_result_signal.emit("uptodate", "uptodate")
                else:
                    self.version_result_signal.emit("error", "error")
            else:  # running from source

                current_version = current_version.removeprefix('local-')
                if '-dirty' in current_version:
                    self.version_result_signal.emit('dirty', 'dirty')
                else:
                    self.version_result_signal.emit("dev", "dev")

        except Exception as e:
            self.error_signal.emit(str(e))
        finally:
            self.__class__.workers.remove(self)
