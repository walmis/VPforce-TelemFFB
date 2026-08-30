"""Compile resources.qrc into resources.py.

Run after adding an image to resources.qrc:

    py makeresources.py

PyQt6 ships no resource compiler - Riverbank dropped pyrcc in the 6.x
line, and Qt's own rcc emits C++ rather than Python.  PySide6's does emit
Python, so that is what generates the file, and the one PySide6 reference
it leaves behind is rewritten to PyQt6 afterwards.  (PyQt5's pyrcc5 also
works, but writes the same images a third larger and in Qt5's format.)

Everything here is checked rather than assumed.  A resource that fails to
register is silent at runtime - the image simply never appears - so each
step that could quietly do nothing stops the script instead.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
QRC = HERE / "resources.qrc"
OUT = HERE / "resources.py"

WRONG_IMPORT = "from PySide6 import QtCore"
RIGHT_IMPORT = "from PyQt6 import QtCore"


def die(message):
    sys.exit(f"makeresources: {message}")


def main():
    rcc = shutil.which("pyside6-rcc")
    if rcc is None:
        die("pyside6-rcc not found. Install it with:  py -m pip install PySide6")
    if not QRC.is_file():
        die(f"{QRC.name} not found")

    declared = len(re.findall(r"<file>", QRC.read_text(encoding="utf-8")))
    print(f"{QRC.name}: {declared} files")

    result = subprocess.run([rcc, str(QRC), "-o", str(OUT)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        die(f"pyside6-rcc failed:\n{result.stderr.strip()}")

    # Edited as bytes so the file comes out exactly as rcc wrote it apart
    # from the one line below.  Reading it as text would decode the CRLFs
    # rcc emits and write back LF, rewriting all hundred thousand lines and
    # showing the whole 4.9 MB as changed on every run.
    data = OUT.read_bytes()
    wrong, right = WRONG_IMPORT.encode(), RIGHT_IMPORT.encode()

    # The generated module imports whichever binding produced it.  Nothing
    # else in the file is binding-specific: the resource data is Qt's own
    # format, which PyQt6 reads unchanged.
    found = data.count(wrong)
    if found != 1:
        # Left alone, the file would import PySide6 - which works on a
        # machine that has it installed, and fails at startup on one that
        # does not.  That is a bug to find now, not after packaging.
        die(f"expected one {WRONG_IMPORT!r} to rewrite, found {found}. "
            f"Check what pyside6-rcc emitted at the top of {OUT.name}.")
    OUT.write_bytes(data.replace(wrong, right))

    # A path that does not exist on disk is skipped by rcc with a warning
    # that is easy to miss in a wall of output.
    if b"qt_resource_name" not in data:
        die("no resource tables in the output - nothing was registered")

    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"{OUT.name}: {size_mb:.1f} MB, import rewritten to PyQt6")
    print("Verify with:  py makeresources.py --check")


def check():
    """Load the generated module under PyQt6 and confirm every path in the
    qrc actually resolves."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QFile
    from PyQt6.QtWidgets import QApplication

    paths = re.findall(r"<file>(.*?)</file>",
                       QRC.read_text(encoding="utf-8"))
    sys.path.insert(0, str(HERE))
    app = QApplication([])                       # noqa: F841  Qt needs one
    import resources                             # noqa: F401  registers them

    # QFile rather than QPixmap: the qrc carries a font as well as images,
    # and asking QPixmap to decode a .ttf reports a failure that is really
    # just the wrong question.  Registration is what this checks.
    missing = [path for path in paths if not QFile(f":/{path}").exists()]
    for path in missing:
        print(f"  MISSING  {path}")
    if missing:
        die(f"{len(missing)} of {len(paths)} resources did not load")
    print(f"all {len(paths)} resources load under PyQt6")


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        main()
