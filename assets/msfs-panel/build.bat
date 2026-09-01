@echo on
setlocal
if "%MSFS_SDK%"=="" (    echo MSFS_SDK environment variable is not set - install the MSFS SDK and set it, e.g.:
    echo   setx MSFS_SDK "C:\MSFS 2024 SDK"
    exit /b 1
)

set PKG=vpforce-telemffb-panel

rem -nomirroring doesn't exist on the MSFS 2024 SDK's fspackagetool (non-mirroring
rem is already the default there - use -mirroring to opt into deleting stray output
rem files instead). -nopause stops it waiting for a keypress after launching the
rem sim to compile the panel.

"%MSFS_SDK%\Tools\bin\fspackagetool.exe" "%PKG%\Build\%PKG%.xml" -nopause
if errorlevel 1 exit /b 1

copy /Y "%PKG%\Build\Packages\%PKG%\Build\%PKG%.spb" "%PKG%\InGamePanels\%PKG%.spb"
if errorlevel 1 exit /b 1

python "%PKG%\build_layout.py"

echo Done. Copy the "%PKG%" folder (NOT its Build subfolder) into your Community folder.
endlocal
