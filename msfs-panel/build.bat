@echo off
setlocal
if "%MSFS_SDK%"=="" (
    echo MSFS_SDK environment variable is not set - install the MSFS SDK and set it, e.g.:
    echo   setx MSFS_SDK "C:\MSFS SDK"
    exit /b 1
)

set PKG=vpforce-telemffb-panel

"%MSFS_SDK%\Tools\bin\fspackagetool.exe" "%PKG%\Build\%PKG%.xml" -nomirroring
if errorlevel 1 exit /b 1

copy /Y "%PKG%\Build\Packages\%PKG%\Build\%PKG%.spb" "%PKG%\InGamePanels\%PKG%.spb"
if errorlevel 1 exit /b 1

python "%PKG%\build_layout.py"

echo Done. Copy the "%PKG%" folder (NOT its Build subfolder) into your Community folder.
endlocal
