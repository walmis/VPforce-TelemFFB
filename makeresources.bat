@echo off
REM Compiles resources.qrc into resources.py.  The work is in
REM makeresources.py, which also rewrites the PySide6 import that
REM pyside6-rcc leaves behind - running the compiler on its own would
REM produce a file that imports the wrong binding.
py "%~dp0makeresources.py" %*
