"""The question asked when a changed setting only takes effect after a
restart: asked from the System Settings dialog and the System menu alike."""
from PyQt6.QtWidgets import QMessageBox


def ask_to_restart(parent, what: str) -> bool:
    """Whether the user wants TelemFFB restarted now for ``what`` (e.g. "The
    theme changed.") to take effect."""
    return QMessageBox.question(
        parent, "Restart Required",
        f"{what} TelemFFB needs to restart for this to take effect.\n\n"
        "Restart now?") == QMessageBox.StandardButton.Yes
